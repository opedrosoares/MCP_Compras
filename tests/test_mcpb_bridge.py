"""Ponte stdio -> HTTP que o `.mcpb` embarca (`mcpb/bridge.js`).

O bundle instalado no Claude Desktop não carrega mais o servidor Python: ele
sobe esta ponte em Node e fala com o deploy remoto. Como o arquivo é JavaScript,
nada da suíte Python o cobria — e o que quebra aqui quebra *na instalação* do
usuário, longe de qualquer log nosso.

Os testes sobem um servidor MCP falso em stdlib e exercitam o que a rede faz de
ruim: resposta em SSE e em JSON puro, 5xx transitório do cold start, sessão
perdida num restart do Railway e servidor inacessível.
"""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node não disponível (é o runtime do .mcpb)"
)

PONTE = Path(__file__).resolve().parents[1] / "mcpb" / "bridge.js"


class _Manipulador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # silencia o log do http.server
        pass

    @property
    def estado(self) -> dict:
        return self.server.estado  # type: ignore[attr-defined]

    # Os nomes do_POST/do_GET/do_DELETE são exigidos pelo BaseHTTPRequestHandler.
    def do_POST(self) -> None:
        tamanho = int(self.headers.get("content-length") or 0)
        corpo = json.loads(self.rfile.read(tamanho) or b"{}")
        self.estado["recebidas"].append((corpo, dict(self.headers)))
        metodo = corpo.get("method")

        if metodo == "initialize":
            self.estado["handshakes"] += 1
            self.estado["contador"] += 1
            sessao = f"s{self.estado['contador']}"
            self.estado["sessoes"].add(sessao)
            self._responder_mensagem(
                {
                    "jsonrpc": "2.0",
                    "id": corpo.get("id"),
                    "result": {"protocolVersion": "2025-06-18", "capabilities": {}},
                },
                extras={"mcp-session-id": sessao},
            )
            return

        if corpo.get("id") is None:  # notificação: nada a responder
            self._responder_vazio(202)
            return

        if self.estado["falhas_5xx"] > 0:
            self.estado["falhas_5xx"] -= 1
            self._responder_vazio(503)
            return

        if self.estado["invalidar_sessoes"]:
            self.estado["invalidar_sessoes"] = False
            self.estado["sessoes"].clear()

        sessao = self.headers.get("mcp-session-id")
        if sessao not in self.estado["sessoes"]:
            self._responder_vazio(404)
            return

        self._responder_mensagem(
            {"jsonrpc": "2.0", "id": corpo["id"], "result": {"eco": metodo, "sessao": sessao}}
        )

    def do_GET(self) -> None:
        self._responder_vazio(405)  # sem canal servidor -> cliente

    def do_DELETE(self) -> None:
        self.estado["deletes"] += 1
        self._responder_vazio(200)

    def _responder_vazio(self, status: int) -> None:
        self.send_response(status)
        self.send_header("content-length", "0")
        self.end_headers()

    def _responder_mensagem(self, mensagem: dict, extras: dict | None = None) -> None:
        texto = json.dumps(mensagem)
        self.send_response(200)
        for chave, valor in (extras or {}).items():
            self.send_header(chave, valor)
        if self.estado["modo_json"]:
            bruto = texto.encode()
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(bruto)))
            self.end_headers()
            self.wfile.write(bruto)
            return
        # SSE: sem content-length, o fim do corpo é o fim da conexão.
        self.send_header("content-type", "text/event-stream")
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(f"event: message\ndata: {texto}\n\n".encode())
        self.close_connection = True


class ServidorFalso:
    def __init__(self) -> None:
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), _Manipulador)
        self.http.estado = {  # type: ignore[attr-defined]
            "recebidas": [],
            "sessoes": set(),
            "contador": 0,
            "handshakes": 0,
            "deletes": 0,
            "falhas_5xx": 0,
            "invalidar_sessoes": False,
            "modo_json": False,
        }
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    @property
    def estado(self) -> dict:
        return self.http.estado  # type: ignore[attr-defined]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.http.server_address[1]}/mcp"

    def parar(self) -> None:
        self.http.shutdown()
        self.http.server_close()


class Ponte:
    """Sobe `bridge.js` como o Claude Desktop faria e conversa por stdio."""

    def __init__(self, url: str) -> None:
        self.processo = subprocess.Popen(
            ["node", str(PONTE)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env={"MCP_COMPRAS_URL": url, "PATH": "/usr/bin:/bin:/usr/local/bin"},
        )
        self.recebidas: queue.Queue = queue.Queue()
        threading.Thread(target=self._ler, daemon=True).start()

    def _ler(self) -> None:
        assert self.processo.stdout
        for linha in self.processo.stdout:
            if linha.strip():
                self.recebidas.put(json.loads(linha))

    def enviar(self, mensagem: dict) -> None:
        assert self.processo.stdin
        self.processo.stdin.write(json.dumps(mensagem) + "\n")
        self.processo.stdin.flush()

    def receber(self, timeout: float = 20.0) -> dict:
        return self.recebidas.get(timeout=timeout)

    def handshake(self) -> dict:
        self.enviar(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
            }
        )
        resposta = self.receber()
        self.enviar({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return resposta

    def parar(self) -> None:
        if self.processo.stdin:
            self.processo.stdin.close()
        try:
            self.processo.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.processo.kill()


@pytest.fixture
def servidor():
    falso = ServidorFalso()
    yield falso
    falso.parar()


@pytest.fixture
def ponte(servidor):
    p = Ponte(servidor.url)
    yield p
    p.parar()


def test_handshake_propaga_a_sessao_nas_chamadas_seguintes(servidor, ponte) -> None:
    """Sem repassar o `Mcp-Session-Id`, toda chamada pós-handshake viraria 404."""
    assert ponte.handshake()["result"]["protocolVersion"] == "2025-06-18"

    ponte.enviar({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    resposta = ponte.receber()

    assert resposta["result"] == {"eco": "tools/list", "sessao": "s1"}
    cabecalhos = servidor.estado["recebidas"][-1][1]
    assert cabecalhos["mcp-session-id"] == "s1"
    assert cabecalhos["mcp-protocol-version"] == "2025-06-18"


def test_aceita_resposta_em_json_puro(servidor, ponte) -> None:
    """A spec permite `application/json` no lugar do SSE; o FastMCP usa SSE, mas
    quem hospeda atrás de um proxy pode receber a outra forma."""
    servidor.estado["modo_json"] = True
    ponte.handshake()

    ponte.enviar({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert ponte.receber()["result"]["eco"] == "tools/list"


def test_retenta_quando_o_servidor_devolve_5xx(servidor, ponte) -> None:
    """Cold start do Railway devolve 5xx por alguns segundos: retentar evita que
    a primeira pergunta do usuário morra."""
    ponte.handshake()
    servidor.estado["falhas_5xx"] = 2

    ponte.enviar({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    resposta = ponte.receber()

    assert resposta["result"]["eco"] == "tools/list"
    assert "error" not in resposta


def test_refaz_o_handshake_quando_a_sessao_expira(servidor, ponte) -> None:
    """Restart do servidor remoto invalida a sessão. A ponte reinicializa por
    baixo e repete a chamada — sem devolver ao cliente uma segunda resposta com
    o id do initialize, que quebraria o cliente."""
    ponte.handshake()
    servidor.estado["invalidar_sessoes"] = True

    ponte.enviar({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    resposta = ponte.receber()

    assert resposta["id"] == 2
    assert resposta["result"] == {"eco": "tools/list", "sessao": "s2"}
    assert servidor.estado["handshakes"] == 2
    assert ponte.recebidas.empty(), "cliente recebeu mensagem a mais no replay do handshake"


def test_avisa_o_cliente_quando_o_servidor_esta_inacessivel(servidor) -> None:
    """Falha de rede não pode deixar a chamada pendurada: o cliente precisa de um
    erro JSON-RPC para mostrar ao usuário."""
    porta_fechada = servidor.http.server_address[1]
    servidor.parar()
    ponte = Ponte(f"http://127.0.0.1:{porta_fechada}/mcp")
    try:
        ponte.enviar(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
            }
        )
        resposta = ponte.receber(timeout=30)
    finally:
        ponte.parar()

    assert resposta["id"] == 1
    assert resposta["error"]["code"] == -32001
    assert "servidor remoto" in resposta["error"]["message"]


def test_encerra_a_sessao_no_servidor_ao_sair(servidor, ponte) -> None:
    """DELETE ao fim evita sessão órfã ocupando memória do deploy remoto."""
    ponte.handshake()
    ponte.enviar({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    ponte.receber()
    ponte.parar()

    assert servidor.estado["deletes"] == 1
