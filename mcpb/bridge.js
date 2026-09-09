#!/usr/bin/env node
/**
 * Ponte stdio -> Streamable HTTP do .mcpb do Compras.gov.br.
 *
 * O Claude Desktop só conversa por stdio com a extensão instalada e o formato
 * .mcpb não tem tipo "remoto" (só `node`, `python`, `binary` e `uv`). Então o
 * bundle embarca este processo: cada mensagem JSON-RPC que chega no stdin vira
 * um POST no endpoint remoto, e as respostas voltam no stdout.
 *
 * Zero dependências, de propósito — é o que torna o .mcpb autocontido:
 *
 * - roda no Node que **acompanha** o Claude Desktop (macOS e Windows), sem
 *   `npm install`;
 * - não precisa de Python na máquina do usuário. O bootstrap antigo pedia
 *   `command: "python"`, binário que nem existe no PATH do macOS (lá é
 *   `python3`), e ainda criava um venv com `pip install` no primeiro start —
 *   daí o "Server disconnected" na instalação;
 * - nada de chave de API no cliente: TRANSPARENCIA_API_KEY, Redis e a máscara
 *   de CPF são configuração do servidor remoto.
 *
 * Fora do caminho feliz, o que este arquivo trata:
 * - resposta em `application/json` **ou** em `text/event-stream` (o FastMCP usa
 *   SSE por padrão, inclusive para responder uma única chamada);
 * - `Mcp-Session-Id`, propagado em todas as chamadas depois do handshake;
 * - sessão perdida (HTTP 404 depois de um restart do Railway): refaz o
 *   handshake por baixo e repete a chamada, sem o cliente perceber;
 * - erro de rede/5xx/429: retenta com backoff (cold start do Railway) e, se
 *   ainda assim falhar, devolve erro JSON-RPC — nunca deixa o pedido pendurado;
 * - fluxo GET do servidor -> cliente, para as notificações que não são resposta
 *   de ninguém (`notifications/tools/list_changed`, logging).
 */

"use strict";

const http = require("node:http");
const https = require("node:https");

const ENDPOINT_PADRAO = "https://mcp-compras.up.railway.app/mcp";
const ENDPOINT = (process.env.MCP_COMPRAS_URL || "").trim() || ENDPOINT_PADRAO;

// Teto por inatividade do socket, não pelo tempo total: tools compostas
// (dossiê de ARP, perfil de fornecedor) somam vários segundos de upstream e o
// SSE pode ficar quieto entre um pedaço e outro.
const TIMEOUT_INATIVIDADE_MS = 180_000;
const TENTATIVAS_REDE = 3;
const BACKOFF_MS = [500, 2_000];

// Delimitador de evento SSE: aceita \n\n, \r\n\r\n e \r\r (spec do EventSource).
const FIM_DE_EVENTO = /\r\n\r\n|\n\n|\r\r/;

let alvo;
try {
  alvo = new URL(ENDPOINT);
} catch {
  process.stderr.write(`[compras-mcp] MCP_COMPRAS_URL inválida: ${ENDPOINT}\n`);
  process.exit(1);
}
if (alvo.protocol !== "https:" && alvo.protocol !== "http:") {
  process.stderr.write(`[compras-mcp] endpoint precisa ser http(s): ${ENDPOINT}\n`);
  process.exit(1);
}
const transporte = alvo.protocol === "http:" ? http : https;

const USER_AGENT = "compras-mcpb-bridge (+https://github.com/opedrosoares/MCP_Compras)";

// ---------------------------------------------------------------- estado

let sessao = null; // Mcp-Session-Id devolvido no handshake
let versaoProtocolo = null; // versão negociada, ecoada em MCP-Protocol-Version
let textoInicializacao = null; // initialize do cliente, guardado para reconectar
let inicializando = null; // Promise do handshake em andamento
let encerrando = false;

// --------------------------------------------------------------- básicos

function log(mensagem) {
  // STDOUT é do protocolo; todo log vai para STDERR.
  process.stderr.write(`[compras-mcp] ${mensagem}\n`);
}

function escrever(linha) {
  process.stdout.write(`${linha}\n`);
}

function enviarAoCliente(objeto) {
  escrever(JSON.stringify(objeto));
}

function dormir(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms).unref());
}

function cabecalhos(extra) {
  const feitos = {
    accept: "application/json, text/event-stream",
    "user-agent": USER_AGENT,
    ...extra,
  };
  if (sessao) feitos["mcp-session-id"] = sessao;
  if (versaoProtocolo) feitos["mcp-protocol-version"] = versaoProtocolo;
  return feitos;
}

function opcoes(metodo, extra) {
  return {
    protocol: alvo.protocol,
    hostname: alvo.hostname,
    port: alvo.port || undefined,
    path: `${alvo.pathname}${alvo.search}`,
    method: metodo,
    headers: cabecalhos(extra),
  };
}

// ------------------------------------------------------------------- SSE

/** Consome um `text/event-stream`, entregando o `data:` de cada evento. */
function lerEventos(resposta, aoMensagem, aoFim) {
  let buffer = "";
  resposta.setEncoding("utf8");
  resposta.on("data", (pedaco) => {
    buffer += pedaco;
    for (;;) {
      const corte = FIM_DE_EVENTO.exec(buffer);
      if (!corte) break;
      const bloco = buffer.slice(0, corte.index);
      buffer = buffer.slice(corte.index + corte[0].length);
      processarEvento(bloco, aoMensagem);
    }
  });
  resposta.on("end", () => aoFim(null));
  resposta.on("error", (falha) => aoFim(falha));
}

function processarEvento(bloco, aoMensagem) {
  const dados = [];
  let idEvento = null;
  for (const linha of bloco.split(/\r\n|\n|\r/)) {
    if (!linha || linha.startsWith(":")) continue; // comentário / keep-alive
    const sep = linha.indexOf(":");
    const campo = sep === -1 ? linha : linha.slice(0, sep);
    let valor = sep === -1 ? "" : linha.slice(sep + 1);
    if (valor.startsWith(" ")) valor = valor.slice(1);
    if (campo === "data") dados.push(valor);
    else if (campo === "id") idEvento = valor;
  }
  if (dados.length) aoMensagem(dados.join("\n"), idEvento);
}

/**
 * Repassa ao cliente o JSON que veio do servidor.
 *
 * Devolve o objeto já parseado (ou null se o servidor mandou algo que não é
 * JSON — descartar é melhor do que sujar o stdout, que é o canal do protocolo).
 */
function repassar(texto) {
  let objeto;
  try {
    objeto = JSON.parse(texto);
  } catch {
    log(`mensagem descartada, JSON inválido vindo do servidor: ${texto.slice(0, 200)}`);
    return null;
  }
  // stdio exige uma mensagem por linha: se o JSON veio quebrado em várias
  // linhas (data: multilinha do SSE), reserializa numa linha só.
  escrever(texto.includes("\n") ? JSON.stringify(objeto) : texto);
  return objeto;
}

// -------------------------------------------------------------- upstream

/**
 * POST de uma mensagem JSON-RPC. Resolve quando a resposta termina; as
 * mensagens do servidor saem por `aoMensagem` conforme chegam.
 */
function postar(corpo, aoMensagem) {
  return new Promise((resolve, reject) => {
    const requisicao = transporte.request(
      opcoes("POST", {
        "content-type": "application/json",
        "content-length": Buffer.byteLength(corpo),
      }),
      (resposta) => {
        const tipo = String(resposta.headers["content-type"] || "").toLowerCase();
        if (tipo.includes("text/event-stream")) {
          lerEventos(resposta, aoMensagem, (falha) =>
            falha
              ? reject(falha)
              : resolve({ status: resposta.statusCode, cabecalhos: resposta.headers, corpo: "" })
          );
          return;
        }
        let recebido = "";
        resposta.setEncoding("utf8");
        resposta.on("data", (pedaco) => (recebido += pedaco));
        resposta.on("end", () =>
          resolve({ status: resposta.statusCode, cabecalhos: resposta.headers, corpo: recebido })
        );
        resposta.on("error", reject);
      }
    );
    requisicao.setTimeout(TIMEOUT_INATIVIDADE_MS, () => {
      requisicao.destroy(new Error(`sem resposta em ${TIMEOUT_INATIVIDADE_MS / 1000}s`));
    });
    requisicao.on("error", reject);
    requisicao.end(corpo);
  });
}

function anotarSessao(cabecalhosResposta) {
  const id = cabecalhosResposta["mcp-session-id"];
  if (id) sessao = Array.isArray(id) ? id[0] : id;
}

function anotarNegociacao(objeto) {
  const versao = objeto && objeto.result && objeto.result.protocolVersion;
  if (versao) versaoProtocolo = versao;
}

/** Refaz o handshake quando o servidor perde a sessão (restart/deploy). */
async function refazerHandshake() {
  if (!textoInicializacao) return false;
  sessao = null;
  // A resposta do initialize é descartada: o cliente já está inicializado e
  // receber um segundo `result` com id repetido só o confundiria.
  const resposta = await postar(textoInicializacao, () => {});
  if (resposta.status >= 400) return false;
  anotarSessao(resposta.cabecalhos);
  await postar(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }), () => {});
  return Boolean(sessao);
}

function responderErro(id, motivo) {
  log(motivo);
  if (id === null) return; // era notificação: não há a quem responder
  enviarAoCliente({
    jsonrpc: "2.0",
    id,
    error: {
      code: -32001,
      message: `MCP Compras.gov.br: ${motivo}. Endpoint: ${ENDPOINT}`,
    },
  });
}

/** Encaminha uma mensagem do cliente, com retry e recuperação de sessão. */
async function encaminhar(texto, mensagem) {
  const id = mensagem && mensagem.id !== undefined && mensagem.id !== null ? mensagem.id : null;
  const ehInicializacao = Boolean(mensagem) && mensagem.method === "initialize";
  let jaRefezSessao = false;
  let tentativasRede = 0;

  for (;;) {
    let jaEmitiu = false;
    try {
      const resposta = await postar(texto, (evento) => {
        const objeto = repassar(evento);
        if (!objeto) return;
        jaEmitiu = true;
        if (ehInicializacao) anotarNegociacao(objeto);
      });

      // 404 com sessão ativa = o servidor reiniciou e não conhece mais a
      // sessão. Refaz o handshake por baixo e repete a chamada uma vez.
      if (resposta.status === 404 && sessao && !ehInicializacao && !jaRefezSessao && !jaEmitiu) {
        jaRefezSessao = true;
        log("sessão expirou no servidor remoto; refazendo o handshake");
        if (await refazerHandshake()) continue;
        responderErro(id, "não foi possível restabelecer a sessão com o servidor remoto");
        return;
      }

      if (resposta.status >= 200 && resposta.status < 300) {
        anotarSessao(resposta.cabecalhos);
        const corpo = resposta.corpo.trim();
        if (corpo) {
          const objeto = repassar(corpo);
          if (objeto && ehInicializacao) anotarNegociacao(objeto);
        }
        return;
      }

      // 5xx/429 são transitórios: cold start do Railway, deploy em andamento.
      if (resposta.status >= 500 || resposta.status === 429) {
        throw new Error(`HTTP ${resposta.status}`);
      }

      responderErro(id, `o servidor remoto recusou a chamada (HTTP ${resposta.status})`);
      return;
    } catch (falha) {
      const motivo = falha && falha.message ? falha.message : String(falha);
      if (jaEmitiu) {
        // Parte da resposta já foi entregue ao cliente; repetir duplicaria
        // mensagens com o mesmo id.
        log(`stream interrompido depois de já ter respondido (${motivo})`);
        return;
      }
      tentativasRede += 1;
      if (tentativasRede >= TENTATIVAS_REDE) {
        responderErro(id, `falha ao falar com o servidor remoto (${motivo})`);
        return;
      }
      const espera = BACKOFF_MS[Math.min(tentativasRede - 1, BACKOFF_MS.length - 1)];
      log(`tentativa ${tentativasRede}/${TENTATIVAS_REDE} falhou (${motivo}); repetindo em ${espera}ms`);
      await dormir(espera);
    }
  }
}

// ------------------------------------------- fluxo servidor -> cliente (GET)

let fluxoDesativado = false;
let falhasDoFluxo = 0;
let ultimoEvento = null;

function agendarFluxo() {
  if (fluxoDesativado || encerrando || !sessao) return;
  const espera = Math.min(1_000 * 2 ** falhasDoFluxo, 30_000);
  setTimeout(abrirFluxoServidor, espera).unref();
}

/**
 * Abre o GET de escuta. É o canal das notificações que o servidor manda por
 * iniciativa própria; se ele não oferecer (405/501), desliga sem barulho.
 */
function abrirFluxoServidor() {
  if (fluxoDesativado || encerrando || !sessao) return;
  const extra = { accept: "text/event-stream" };
  if (ultimoEvento) extra["last-event-id"] = ultimoEvento;

  const requisicao = transporte.request(opcoes("GET", extra), (resposta) => {
    if (resposta.statusCode === 405 || resposta.statusCode === 501) {
      fluxoDesativado = true; // servidor não implementa o canal; segue sem ele
      resposta.resume();
      return;
    }
    if (resposta.statusCode !== 200) {
      resposta.resume();
      falhasDoFluxo += 1;
      agendarFluxo();
      return;
    }
    falhasDoFluxo = 0;
    lerEventos(
      resposta,
      (evento, idEvento) => {
        if (idEvento) ultimoEvento = idEvento;
        repassar(evento);
      },
      () => agendarFluxo()
    );
  });

  requisicao.setTimeout(0); // o canal fica ocioso de propósito
  requisicao.on("error", () => {
    falhasDoFluxo += 1;
    agendarFluxo();
  });
  requisicao.end();
}

// ----------------------------------------------------------------- stdio

function tratarLinha(linha) {
  let mensagem;
  try {
    mensagem = JSON.parse(linha);
  } catch {
    log(`linha do cliente descartada, JSON inválido: ${linha.slice(0, 200)}`);
    return;
  }

  if (mensagem && mensagem.method === "initialize") {
    textoInicializacao = linha;
    inicializando = encaminhar(linha, mensagem)
      .catch((falha) => log(`falha inesperada no handshake: ${falha}`))
      .then(() => {
        inicializando = null;
        abrirFluxoServidor();
      });
    return;
  }

  // Enquanto o handshake não termina, o resto espera: sem Mcp-Session-Id o
  // servidor recusaria as chamadas seguintes.
  if (inicializando) {
    inicializando.then(() => encaminhar(linha, mensagem)).catch((falha) => log(`falha inesperada: ${falha}`));
    return;
  }
  encaminhar(linha, mensagem).catch((falha) => log(`falha inesperada: ${falha}`));
}

let entrada = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (pedaco) => {
  entrada += pedaco;
  for (;;) {
    const quebra = entrada.indexOf("\n");
    if (quebra === -1) break;
    const linha = entrada.slice(0, quebra).trim();
    entrada = entrada.slice(quebra + 1);
    if (linha) tratarLinha(linha);
  }
});
process.stdin.on("end", encerrar);
process.stdout.on("error", () => process.exit(0)); // cliente fechou o pipe

/** Avisa o servidor que a sessão acabou (spec pede DELETE) e sai. */
function encerrar() {
  if (encerrando) return;
  encerrando = true;
  if (!sessao) {
    process.exit(0);
  }
  const requisicao = transporte.request(opcoes("DELETE", {}), (resposta) => {
    resposta.resume();
    resposta.on("end", () => process.exit(0));
  });
  requisicao.setTimeout(3_000, () => {
    requisicao.destroy();
    process.exit(0);
  });
  requisicao.on("error", () => process.exit(0));
  requisicao.end();
}

for (const sinal of ["SIGINT", "SIGTERM"]) process.on(sinal, encerrar);

log(`ponte ativa -> ${ENDPOINT}`);
