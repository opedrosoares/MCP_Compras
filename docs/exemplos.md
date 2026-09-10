# Exemplos de uso

Cada exemplo tem duas leituras: **o que você pergunta** (em português, sem saber nome de tool) e
**o que acontece por baixo** (a sequência de tools, para quem quer conferir ou reproduzir o passo
a passo manualmente).

---

## Pesquisa de preços para um ETP

> *"Preciso da pesquisa de preços de cadeiras ergonômicas para um ETP, no padrão da IN 65/2021."*

Por baixo:

1. `compras_catmat_buscar` com `termo="cadeira ergonomica"` → obter `codigo_item_catalogo`
2. `compras_pesquisar_precos_para_etp` (composta) com `tipo="material"` → mediana/média/desvio + descarte IQR
3. `compras_pgc_por_catalogo` para ver o que outros órgãos planejaram comprar
4. `compras_arp_listar` com `apenas_vigentes=True` → atas vigentes para possível adesão

---

## Ler a especificação técnica real por trás de um item genérico

> *"Qual GPU está por trás do CATMAT genérico de 'microcomputador' que o órgão X comprou?"*

Por baixo:

1. `compras_pncp_contratacoes_publicacao` (ou `compras_arp_listar`) → obter `cnpj`, `ano` e `sequencial` da compra
2. `compras_pncp_contratacao_arquivos` → lista Edital, Termo de Referência, ETP e Projeto Básico com URL de download
3. Baixar a `url` com um GET simples — o Edital costuma vir como ZIP (às vezes ZIP dentro de ZIP) com o TR dentro

É o caminho para descobrir a especificação real que um código de catálogo genérico esconde.

---

## Acompanhar aditivos de uma ata de registro de preços

> *"Essa ata teve reequilíbrio ou prorrogação desde a publicação?"*

Por baixo:

1. `compras_arp_listar` → obter `numeroControlePncpAta` e o sequencial da ata dentro da compra
2. `compras_pncp_ata_arquivos` → ata original + aditivos de reequilíbrio/prorrogação, ordenáveis por `dataPublicacaoPncp`

---

## Análise de fornecedor antes da homologação

> *"O fornecedor CNPJ 00.000.000/0001-91 tem sanção vigente, e quais contratos ele tem hoje?"*

Por baixo:

1. `compras_perfil_fornecedor_completo` (composta) com o CNPJ — uma chamada devolve cadastro +
   sanções (CEIS/CNEP/CEPIM/CEAF) + contratos vigentes + dados da Receita Federal

---

## Inventário de contratos a renovar

> *"Quais contratos vencem nos próximos 90 dias e qual o histórico de aditivos de cada um?"*

Por baixo:

1. `compras_contratos_listar_por_fim_vigencia` com `data_fim_vigencia` próxima
2. Para cada contrato relevante: `compras_contrato_historico_aditivos`, `compras_contrato_ocorrencias`

---

## Antes de uma demonstração ou de instruir um processo

> *"As APIs estão todas no ar agora?"*

Por baixo:

1. `compras_healthcheck(profundidade="rotas")` — probe real contra o upstream em ~30s, retorna
   `pronto_para_uso` e qual módulo está degradado ou fora, se algum

É a resposta ao vivo; o retrato mais recente conhecido está em
[Qualidade das APIs públicas](qualidade-das-apis.md).

---

Quer o catálogo completo do que existe? [Tools, prompts e resources](ferramentas.md).
