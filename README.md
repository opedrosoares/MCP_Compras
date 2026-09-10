# MCP Compras.gov.br

[![M8ven Score](https://m8ven.ai/badge/mcp/opedrosoares-mcp-compras-1wrbus?v=bf512ced4090ecbb6958bbd911c20716)](https://m8ven.ai/mcp/opedrosoares-mcp-compras-1wrbus)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![FastMCP](https://img.shields.io/badge/FastMCP-2.x-informational)](https://github.com/jlowin/fastmcp)

### Pergunte sobre compras públicas em português. Receba a resposta com dado oficial.

Este projeto liga o Claude — ou qualquer assistente de IA compatível com MCP — às APIs públicas do
**Compras.gov.br**, do **PNCP** e do **Portal da Transparência**. Em vez de abrir cinco portais e
cruzar planilhas, você pergunta:

> *"Qual o preço médio que o governo federal pagou em cadeiras ergonômicas nos últimos 12 meses?"*
>
> *"O fornecedor do CNPJ 00.000.000/0001-91 tem alguma sanção vigente?"*
>
> *"Existe ata de registro de preços vigente, com saldo, para notebooks?"*

Feito para quem trabalha com **planejamento de contratação** e **execução contratual**: ETP, TR,
pesquisa de preços no padrão da IN SEGES/ME 65/2021, adesão a ata (carona), due diligence de
fornecedor, benchmark entre órgãos.

## Instalar em 1 minuto (Claude Desktop)

1. [**Baixe o `compras.mcpb`**](https://github.com/opedrosoares/MCP_Compras/releases/latest/download/compras.mcpb)
2. Abra o arquivo com **duplo-clique** — o Claude Desktop instala sozinho
3. Comece a perguntar

São 23 KB e **nada é instalado na sua máquina**: nem Python, nem chave de API, nem configuração.

[![Pesquisa de preços para ETP em 1 minuto | MCP Compras.gov.br](https://img.youtube.com/vi/ERIxOW1UyzA/hqdefault.jpg)](https://youtu.be/ERIxOW1UyzA)

> Usa **Claude Code**, **Cursor**, ou quer rodar o servidor você mesmo?
> O [guia de instalação](https://github.com/opedrosoares/MCP_Compras/blob/main/docs/instalacao.md) tem os três caminhos.

## O que dá para fazer

| Se você precisa… | Pergunte algo como |
|------------------|--------------------|
| **Pesquisa de preços para um ETP** | *"Monte a pesquisa de preços de cadeira ergonômica no padrão da IN 65/2021"* — vem mediana, média, desvio e descarte de outliers |
| **Checar um fornecedor** | *"Esse CNPJ tem sanção no CEIS, CNEP, CEPIM ou CEAF?"* — cadastro, sanções, contratos e dados da Receita em uma resposta |
| **Achar uma ata para carona** | *"Tem ata vigente com saldo para esse item?"* — inclui saldo por item e adesões já feitas |
| **Ler o Termo de Referência de outro órgão** | *"Baixe o Edital e o TR dessa contratação do PNCP"* — arquivos com URL de download direto |
| **Planejar com base no que os outros compram** | *"O que os órgãos federais planejaram comprar desse item este ano?"* — PGC e PCA |
| **Acompanhar contratos vigentes** | *"Quais contratos vencem em 90 dias e quais tiveram aditivo?"* — vigência, aditivos, fiscais, empenhos |

Os passos por trás de cada um estão em
[Exemplos de uso](https://github.com/opedrosoares/MCP_Compras/blob/main/docs/exemplos.md).

## O que vem dentro

**100 tools + 6 prompts + 6 resources**, cobrindo catálogo (CATMAT/CATSER), pesquisa de preços,
planejamento (PGC/PCA), atas de registro de preços, contratações pela Lei 14.133 e pela 8.666,
contratos, fornecedores, sanções, PNCP, órgãos e UASGs, indicadores e analítica.

Cinco fontes oficiais, todas públicas: **Dados Abertos Compras**, **PNCP**, **Portal da
Transparência (CGU)**, **Comprasnet Contratos** e **BrasilAPI/Receita**. Só a de sanções pede uma
chave — gratuita, e apenas para quem roda o próprio servidor.

Catálogo completo em
[Tools, prompts e resources](https://github.com/opedrosoares/MCP_Compras/blob/main/docs/ferramentas.md).

## Documentação

| Página | Para quê |
|--------|----------|
| [Instalação](https://github.com/opedrosoares/MCP_Compras/blob/main/docs/instalacao.md) | Os três caminhos, conexão com clientes MCP e requisitos |
| [Como funciona a extensão `.mcpb`](https://github.com/opedrosoares/MCP_Compras/blob/main/docs/extensao-mcpb.md) | O que o bundle de 23 KB faz, e por que não instala nada |
| [Configuração](https://github.com/opedrosoares/MCP_Compras/blob/main/docs/configuracao.md) | Variáveis de ambiente e a chave gratuita da CGU |
| [Deploy remoto (Railway)](https://github.com/opedrosoares/MCP_Compras/blob/main/docs/deploy-railway.md) | Subir a sua própria instância, passo a passo |
| [Tools, prompts e resources](https://github.com/opedrosoares/MCP_Compras/blob/main/docs/ferramentas.md) | Catálogo completo e fontes de dados |
| [Exemplos de uso](https://github.com/opedrosoares/MCP_Compras/blob/main/docs/exemplos.md) | Fluxos reais, passo a passo |
| [Qualidade das APIs públicas](https://github.com/opedrosoares/MCP_Compras/blob/main/docs/qualidade-das-apis.md) | Diagnóstico do upstream, com causa raiz e contorno |
| [Arquitetura](https://github.com/opedrosoares/MCP_Compras/blob/main/docs/arquitetura.md) | Padrões internos, cache, LGPD, testes de contrato |

Também: [Changelog](https://github.com/opedrosoares/MCP_Compras/blob/main/CHANGELOG.md) ·
[Roadmap](https://github.com/opedrosoares/MCP_Compras/blob/main/ROADMAP.md) ·
[Issues upstream](https://github.com/opedrosoares/MCP_Compras/blob/main/ISSUES_UPSTREAM.md)

## Perguntas frequentes

**Preciso saber programar?** Não, se você usa o Claude Desktop: é baixar o arquivo e dar
duplo-clique.

**Preciso instalar Python?** Só se quiser rodar o servidor na sua máquina. A extensão não precisa.

**Custa alguma coisa?** Não. Todas as APIs usadas são públicas e gratuitas, e o projeto é MIT.

**Os dados são oficiais?** Sim — vêm direto das APIs do governo, sem intermediário e sem base
própria. Nada é inventado nem armazenado: o servidor só consulta e organiza.

**E os dados pessoais?** CPFs de servidores vêm mascarados por padrão (`123.***.***-45`), conforme
a LGPD.

**Alguma consulta voltou vazia. É bug?** Peça `compras_healthcheck` ao assistente: em ~30s ele
testa cada API e diz qual está fora do ar. As limitações já conhecidas do upstream estão
documentadas em
[Qualidade das APIs públicas](https://github.com/opedrosoares/MCP_Compras/blob/main/docs/qualidade-das-apis.md).

## Contribuir

Issues e pull requests são bem-vindos. Bugs que estão do lado das APIs do governo — e não deste
servidor — ficam catalogados, com reprodução, em
[`ISSUES_UPSTREAM.md`](https://github.com/opedrosoares/MCP_Compras/blob/main/ISSUES_UPSTREAM.md),
prontos para envio aos órgãos mantenedores.

## Licença

MIT — veja [LICENSE](LICENSE).

## Status

**v0.4.0** — 100 tools + 6 prompts + 6 resources, em produção (Railway + Redis). Cada release
recente foi validada em bateria de testes ponta a ponta contra o ambiente de produção, não apenas
local — ver [Changelog](https://github.com/opedrosoares/MCP_Compras/blob/main/CHANGELOG.md).

<!-- Prova de propriedade do MCP Registry oficial: o validador procura este
     token na long_description publicada no PyPI. Não remover. -->
<!-- mcp-name: io.github.opedrosoares/mcp-compras -->
