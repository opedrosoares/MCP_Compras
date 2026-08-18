"""MCP Prompts — templates pré-definidos que o cliente lista para o usuário.

Diferente de tools (que o LLM invoca), prompts são selecionados pelo usuário
no cliente MCP (Claude Desktop, Cursor etc.) e expandidos em uma mensagem
estruturada que orienta o LLM a executar um fluxo típico usando as tools
disponíveis.

Cada prompt aqui corresponde a um caso de uso recorrente do analista de
licitação. O texto é em português porque o usuário-alvo trabalha com a
legislação brasileira (Lei 14.133/2021).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import Field

from compras_mcp.mcp_instance import mcp


@mcp.prompt(
    name="analisar_contratacao_pncp",
    description=(
        "Produz um checklist de viabilidade para uma contratação publicada no PNCP: "
        "resumo do objeto, valor, prazos, itens críticos, documentos do edital, "
        "riscos. Combina compras_pncp_contratacao_itens + compras_pncp_contratacao_item_resultados."
    ),
    tags={"pncp", "edital", "due-diligence"},
)
def analisar_contratacao_pncp(
    cnpj_orgao: Annotated[
        str,
        Field(description="CNPJ do órgão que publicou a contratação (14 dígitos)."),
    ],
    ano: Annotated[int, Field(description="Ano da contratação (ex.: 2025).")],
    sequencial: Annotated[
        int, Field(description="Sequencial da contratação no órgão (ex.: 12345).")
    ],
) -> str:
    return (
        f"Analise a contratação PNCP do CNPJ {cnpj_orgao}, ano {ano}, sequencial {sequencial}. "
        "Produza um relatório com:\n\n"
        "1. **Resumo executivo** (3-5 linhas)\n"
        "   - Use `compras_pncp_contratacao_itens` para obter dados base.\n"
        "   - Objeto, modalidade, órgão, UF, esfera.\n"
        "   - Valor estimado vs homologado.\n"
        "   - Datas-chave: publicação, abertura, encerramento de proposta.\n\n"
        "2. **Itens críticos**\n"
        "   - Liste os itens e identifique os de maior valor unitário.\n"
        "   - Sinalize critério de julgamento e quantidade.\n"
        "   - Para cada item relevante, opcionalmente chame "
        "`compras_pncp_contratacao_item_resultados` para ver lances/vencedor.\n\n"
        "3. **Checklist de viabilidade** (sim / não / depende)\n"
        "   - CNAE exigido foi explicitado? Empresa atende?\n"
        "   - Prazo de proposta é realista?\n"
        "   - Há exigências de atestados, certificações, capital mínimo?\n"
        "   - Há indícios de direcionamento (especificação fechada, prazo curto)?\n\n"
        "4. **Riscos e bandeiras vermelhas**\n"
        "   - Modalidade compatível com valor?\n"
        "   - Houve republicação?\n"
        "   - Vencedores anteriores do mesmo órgão (cruzar com "
        "`compras_pncp_contrato_por_orgao`).\n\n"
        "Comece chamando `compras_pncp_contratacao_itens(cnpj_orgao=\"" + cnpj_orgao + "\", "
        f"ano={ano}, sequencial={sequencial})`."
    )


@mcp.prompt(
    name="panorama_orgao_360",
    description=(
        "Perfil 360° de um órgão público comprador: identificação, contratações "
        "publicadas no último ano, principais fornecedores, PCA do ano corrente. "
        "Combina compras_orgao_consultar + compras_pncp_contratacao_por_orgao + "
        "compras_pncp_pca_por_usuario."
    ),
    tags={"orgao", "perfil", "estrategia"},
)
def panorama_orgao_360(
    codigo_orgao: Annotated[
        str,
        Field(
            description=(
                "Código SIORG/UASG do órgão OU CNPJ (14 dígitos). "
                "Quando souber só o CNPJ, use o CNPJ; quando souber o "
                "código SIORG, use ele."
            )
        ),
    ],
    foco: Annotated[
        str | None,
        Field(
            default=None,
            description="Área temática opcional (ex.: 'TI', 'obras', 'saúde').",
        ),
    ] = None,
) -> str:
    foco_txt = f" com foco em **{foco}**" if foco else ""
    ano_atual = date.today().year
    return (
        f"Construa um perfil 360° do órgão {codigo_orgao}{foco_txt}.\n\n"
        "Etapas:\n\n"
        f"1. **Identificação** — `compras_orgao_consultar(codigo_orgao={codigo_orgao})` "
        "(se o input parecer código SIORG) ou liste via `compras_orgao_listar` "
        "filtrando por CNPJ.\n"
        "   - Poder, esfera, natureza jurídica, vinculação.\n\n"
        "2. **Contratações recentes** — `compras_pncp_contratacao_por_orgao` "
        f"com CNPJ do órgão, ano {ano_atual}.\n"
        "   - Top 5 contratações por valor estimado.\n"
        "   - Distribuição por modalidade.\n"
        "   - Tipos de objeto recorrentes.\n\n"
        "3. **Contratos vigentes** — `compras_pncp_contrato_por_orgao` "
        f"no ano {ano_atual}.\n"
        "   - Top 5 fornecedores por valor agregado.\n"
        "   - Há concentração em poucos fornecedores?\n\n"
        "4. **Planejamento** — `compras_pncp_pca_por_usuario` ou "
        f"`compras_pncp_pca_listar` para {ano_atual}.\n"
        "   - O que o órgão planeja comprar?\n"
        "   - Janelas de oportunidade (datas previstas).\n\n"
        "5. **Síntese estratégica**\n"
        "   - Para um fornecedor que quer atender este órgão, qual o caminho "
        "de entrada (modalidade típica, porte)?\n"
        "   - Concorrentes incumbentes.\n"
        + (f"   - Foco específico: análise da área **{foco}**.\n" if foco else "")
    )


@mcp.prompt(
    name="dossie_due_diligence_fornecedor",
    description=(
        "Dossiê completo de due diligence de um fornecedor: cadastro, sanções "
        "(CEIS/CNEP/CEPIM/CEAF + leniência), impedimentos, e contratos quando "
        "houver órgão informado. Combina compras_perfil_fornecedor_completo + "
        "compras_fornecedor_contratos_por_item."
    ),
    tags={"fornecedor", "due-diligence", "sancoes"},
)
def dossie_due_diligence_fornecedor(
    cnpj: Annotated[
        str,
        Field(description="CNPJ do fornecedor (14 dígitos, com ou sem pontuação)."),
    ],
    codigo_orgao_contratos: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Código do órgão para listar contratos do fornecedor naquele órgão. "
                "Se omitido, o passo de listagem de contratos é pulado."
            ),
        ),
    ] = None,
) -> str:
    org_txt = (
        f"\n5. **Histórico em contratos** — `compras_contratos_listar(codigo_orgao={codigo_orgao_contratos}, "
        f"ni_fornecedor=\"{cnpj}\")`.\n"
        "   - Quantidade, valor agregado, distribuição por modalidade.\n"
        "   - Vigência dos contratos ativos.\n"
        if codigo_orgao_contratos
        else "\n5. **Histórico em contratos** — pulado (codigo_orgao_contratos não informado).\n"
    )
    return (
        f"Produza um dossiê de due diligence do fornecedor CNPJ {cnpj}.\n\n"
        "Etapas:\n\n"
        f"1. **Perfil consolidado** — `compras_perfil_fornecedor_completo(cnpj=\"{cnpj}\")`\n"
        "   - Razão social, situação cadastral, CNAEs, porte.\n"
        "   - Sanções ativas (CEIS, CNEP, CEPIM).\n"
        "   - Impedimentos Comprasnet.\n\n"
        f"2. **CEAF e leniência** — `compras_sancao_ceaf(cnpj=\"{cnpj}\")` e "
        f"`compras_sancao_acordos_leniencia(cnpj_sancionado=\"{cnpj}\")`.\n"
        "   - O CEAF cobre servidores expulsos (PEP-relacionado); leniência "
        "indica colaboração premiada.\n\n"
        "3. **Cadastro detalhado** (enriquecimento Receita Federal)\n"
        f"   - `compras_fornecedor_cnpj_receita(cnpj=\"{cnpj}\")` para QSA, "
        "capital social, data de início, atividades secundárias. Usa BrasilAPI "
        "por padrão; trocável via env `CNPJ_PROVIDER=minhareceita`.\n\n"
        "4. **Tem alguma restrição?**\n"
        "   - Se `tem_alguma_restricao` = true no perfil consolidado, alertar.\n"
        + org_txt
        + "\n6. **Síntese final** (4-5 linhas)\n"
        "   - Apenas fatos do dado público; sem opinar sobre 'confiabilidade'.\n"
        "   - Recomendação operacional para o setor de licitação.\n\n"
        "**Importante**: sanções e impedimentos têm efeitos legais distintos. "
        "CEIS impede contratar com qualquer ente público; CNEP atinge empresa "
        "pela Lei Anticorrupção; CEPIM impede convênios; CEAF é sobre servidor. "
        "Cite a base de cada restrição encontrada."
    )


@mcp.prompt(
    name="oportunidades_carona_arp",
    description=(
        "Encontra atas de registro de preço vigentes com saldo disponível para "
        "carona — oportunidade para órgãos que querem aderir e para fornecedores "
        "que já são vencedores. Combina compras_arp_listar + compras_arp_saldo_item."
    ),
    tags={"arp", "carona", "oportunidade"},
)
def oportunidades_carona_arp(
    palavra_chave: Annotated[
        str,
        Field(description="Termo de busca (ex.: 'notebook', 'uniformes', 'limpeza')."),
    ],
    uf: Annotated[
        str | None,
        Field(
            default=None,
            min_length=2,
            max_length=2,
            description="Filtro opcional de UF do órgão gerenciador (sigla 2 letras).",
        ),
    ] = None,
    apenas_vigentes: Annotated[
        bool,
        Field(
            default=True,
            description="Se True, filtra atas com vigência ainda aberta (fim_vigencia >= hoje).",
        ),
    ] = True,
) -> str:
    uf_txt = f" (preferência por UF {uf}, filtrada client-side)" if uf else ""
    flag = "apenas vigentes" if apenas_vigentes else "incluindo expiradas"
    hoje = date.today().isoformat()
    daqui_um_ano = date(date.today().year + 1, date.today().month, date.today().day).isoformat()
    return (
        f"Identifique oportunidades de carona em atas de registro de preço "
        f"para **{palavra_chave}**{uf_txt}, {flag}.\n\n"
        "**Importante sobre limitações de filtragem upstream**: dois pontos.\n\n"
        "(a) As tools de ARP do PNCP **não aceitam filtro por palavra-chave "
        "nem UF**. O filtro precisa ser aplicado client-side nos campos "
        "`objetoCompra` / `nomeUnidadeOrgao` / `unidadeOrgao.ufSigla`.\n\n"
        "(b) O filtro textual `termo` de `compras_catmat_buscar` **está "
        "quebrado upstream** desde meados de 2026 — devolve o universo CATMAT "
        "(~340k itens). Para classificar `{palavra_chave}` em CATMATs reais, "
        "use o **workflow estrutural**: listar grupos → escolher → listar "
        "classes daquele grupo → escolher → buscar reforçado por "
        "`codigo_grupo` (e idealmente `codigo_classe`). Se o agente chamar "
        "`compras_catmat_buscar(termo=...)` sem código estrutural, o payload "
        "vem com `_aviso_filtro` e os primeiros itens começam por arma de "
        "fogo, não pelo que foi pedido.\n\n"
        "Para reduzir o universo, classificamos antes em CATMATs estruturais, "
        "depois cruzamos com ARPs.\n\n"
        "Etapas:\n\n"
        f"1. **Classificar '{palavra_chave}' em CATMATs (workflow estrutural)**\n"
        "   - `compras_catmat_listar_grupos(tamanho_pagina=99)` para enumerar "
        "os ~70 grupos do CATMAT.\n"
        f"   - Identifique 1-2 grupos onde '{palavra_chave}' faria sentido "
        "(ex.: `70=Equipamentos para Processamento Automático de Dados` para "
        "TI, `73=Equipamentos para Preparo e Servimento de Comida` para "
        "cozinha industrial, etc.).\n"
        "   - Para cada grupo selecionado, "
        "`compras_catmat_listar_classes(codigo_grupo=<GRUPO>)` → identifique "
        "1-2 classes mais aderentes.\n"
        "   - Finalmente, "
        f"`compras_catmat_buscar(termo=\"{palavra_chave}\", "
        "codigo_grupo=<GRUPO>, codigo_classe=<CLASSE>, tamanho_pagina=50)`. "
        "**Sem `codigo_grupo` o filtro textual é ignorado** e o agente recebe "
        "armas de fogo no topo.\n"
        "   - Se o payload trouxer `_aviso_filtro`, ignore o resultado e "
        "refaça com filtros estruturais reforçados.\n"
        "   - Selecione 3-5 códigos CATMAT realmente aderentes.\n\n"
        "2. **Buscar contratações similares por CATMAT** (federa Dados Abertos "
        "+ PNCP) — `compras_buscar_contratacoes_similares` para cada CATMAT "
        f"selecionado, `periodo_meses=12`"
        + (f", `uf=\"{uf}\"`" if uf else "")
        + ".\n"
        "   - O retorno traz contratações que podem ter virado ARP. "
        "Cruze o `numero_controle_pncp` para a próxima etapa.\n\n"
        f"3. **Buscar ARPs vigentes contendo '{palavra_chave}'** "
        "(filtro server-side novo em v0.3.6) — "
        f"`compras_arp_buscar_por_objeto(palavra_chave=\"{palavra_chave}\", "
        f"data_vigencia_final_min=\"{hoje}\", "
        f"data_vigencia_final_max=\"{daqui_um_ano}\", "
        "max_paginas_varridas=10, max_resultados=20)`.\n"
        "   - A tool pagina e filtra internamente o campo `objeto`; varre "
        "até 5.000 ARPs e para ao achar 20 matches. Antes da v0.3.6 era "
        "preciso filtrar manualmente em 339 páginas — agora 1 chamada.\n"
        "   - Se `matches=0`, suba `max_paginas_varridas` (até 50). Se "
        "ainda 0, o termo é raro na janela — tente variantes.\n"
        + (
            f"   - **Filtro UF '{uf}' precisa ser feito client-side**: "
            "o schema upstream da ARP não traz UF. Para cada match, "
            "chame `compras_uasg_consultar(codigo_uasg=<codigoUnidadeGerenciadora>)` "
            f"e compare `unidade.uf == \"{uf}\"`.\n"
            if uf
            else ""
        )
        + "   - Cruze, se quiser, com os `numero_controle_pncp` da etapa 2 "
        "para priorizar ARPs originadas de pregões análogos.\n\n"
        "4. **Dossiê detalhado das top 5 ARPs candidatas** — "
        "`compras_montar_dossie_arp(numero_controle_pncp=<NCP>)` para cada.\n"
        "   - Dossiê traz ata + itens + saldo + adesões + unidades participantes.\n\n"
        "5. **Filtrar itens com saldo > 0**\n"
        "   - Para cada item da ata, `compras_arp_saldo_item(numero_controle_pncp, "
        "numero_item)` confirma quantidade registrada vs já consumida.\n"
        "   - Vencedor do item, valor unitário homologado, vigência restante.\n\n"
        "6. **Ranking final** ordenado por:\n"
        "   `(saldo_qtd × valor_unitário × dias_de_vigência_restantes)`\n\n"
        "7. **Avisos ao usuário**\n"
        "   - Adesão a ARP exige autorização do órgão gerenciador.\n"
        "   - Existem limites: 50% do quantitativo por órgão aderente, 200% "
        "no total (Decreto 11.462/2023 art. 32).\n"
        "   - O fornecedor pode recusar a adesão.\n"
        "   - Para órgãos federais aderindo a ARP estadual/municipal, regras "
        "específicas do Decreto 11.462 aplicam-se."
    )


@mcp.prompt(
    name="montar_etp_pesquisa_precos",
    description=(
        "Monta a seção de pesquisa de preços de um ETP no padrão IN SEGES/ME "
        "65/2021: pelo menos 3 fontes, estatística descritiva, descarte de "
        "outliers (IQR), justificativa metodológica. Usa "
        "compras_pesquisar_precos_para_etp."
    ),
    tags={"etp", "preco", "in-65-2021"},
)
def montar_etp_pesquisa_precos(
    codigo_catmat: Annotated[
        int | None,
        Field(
            default=None,
            description="Código CATMAT do material (use este OU codigo_catser).",
        ),
    ] = None,
    codigo_catser: Annotated[
        int | None,
        Field(
            default=None,
            description="Código CATSER do serviço (use este OU codigo_catmat).",
        ),
    ] = None,
    descricao_objeto: Annotated[
        str,
        Field(
            description=(
                "Descrição livre do objeto/serviço como aparecerá no TR. "
                "Usado no relatório final."
            ),
            min_length=5,
        ),
    ] = "",
    quantidade_estimada: Annotated[
        float | None,
        Field(
            default=None,
            ge=0,
            description="Quantidade total prevista (para cálculo de valor estimado).",
        ),
    ] = None,
    janela_meses: Annotated[
        int,
        Field(default=12, ge=3, le=24, description="Janela em meses para buscar preços."),
    ] = 12,
) -> str:
    # IMPORTANTE: a tool real `compras_pesquisar_precos_para_etp` aceita
    # `codigo_item_catalogo` e `periodo_meses` (não `codigo_catmat` /
    # `janela_meses` que eram os nomes legados deste prompt antes da v0.3.11).
    # Aceita também `tipo: "material" | "servico"`. Achado bateria A rodada 5.
    if codigo_catmat is None and codigo_catser is None:
        catalogo_hint = (
            "Como nenhum código foi passado, primeiro use "
            f"`compras_catmat_buscar(termo='{descricao_objeto}')` (com "
            "`codigo_grupo` + `codigo_classe` — o filtro textual upstream "
            "está quebrado, ver docstring da tool) ou `compras_catser_consultar`.\n"
            "   - **Atenção crítica**: confirme que a descrição oficial do "
            "CATMAT escolhido bate com a spec do TR (RAM, SSD, processador). "
            "CATMATs antigos do PDM são frequentemente usados como 'balde "
            "universal' por órgãos (ex.: CATMAT 451899 do PDM 8435 'NOTEBOOK' "
            "tem spec oficial 'até 4GB RAM, sem SSD' mas órgãos cotam i5/16GB "
            "nele). Heterogeneidade alta no payload é sinal disso."
        )
        chamada = (
            "compras_pesquisar_precos_para_etp(tipo='material', "
            "codigo_item_catalogo=<CATMAT descoberto>, periodo_meses=12)"
        )
    else:
        catalogo_hint = ""
        if codigo_catmat:
            chamada = (
                f"compras_pesquisar_precos_para_etp(tipo='material', "
                f"codigo_item_catalogo={codigo_catmat}, "
                f"periodo_meses={janela_meses})"
            )
        else:
            chamada = (
                f"compras_pesquisar_precos_para_etp(tipo='servico', "
                f"codigo_item_catalogo={codigo_catser}, "
                f"periodo_meses={janela_meses})"
            )
    qtd_txt = (
        f"\n   - Multiplique o valor unitário recomendado por **{quantidade_estimada}** "
        "para chegar ao valor total estimado.\n"
        if quantidade_estimada
        else ""
    )
    return (
        f"Monte a seção de pesquisa de preços do ETP para: **{descricao_objeto}**.\n\n"
        "Base normativa: IN SEGES/ME 65/2021 (art. 5º — métodos e número mínimo "
        "de fontes) c/c Lei 14.133/2021 art. 23.\n\n"
        + (catalogo_hint + "\n\n" if catalogo_hint else "")
        + f"1. **Coleta** — chame `{chamada}`.\n"
        "   - A tool agrega Dados Abertos (preços homologados de órgãos federais), "
        "calcula mediana/média/desvio, descarta outliers via IQR (Tukey) e "
        "**detecta heterogeneidade**.\n\n"
        "2. **Inspecionar heterogeneidade da amostra** (v0.3.7+)\n"
        "   - O payload traz `estatisticas.coeficiente_variacao` (CV). "
        "Se CV > 0,5 OU razão `maximo/minimo` > 3×, o payload **também** "
        "trazerá uma lista `clusters` particionando a amostra por gaps "
        "relativos e um campo `aviso_heterogeneidade`.\n"
        "   - **Se houver clusters**, NÃO use a mediana global. Examine "
        "cada cluster, identifique qual perfil técnico ele representa "
        "(via marcas/órgãos nos `registros`), e **escolha explicitamente "
        "qual cluster** corresponde à spec do TR. Documente a escolha como "
        "ajuste motivado do recorte amostral (IN 65/2021 art. 9º §1º).\n"
        "   - Se a heterogeneidade for grande mesmo dentro do cluster "
        "compatível, suspeite de **CATMAT-balde**: o código escolhido "
        "agrega produtos que tecnicamente não são iguais. Confirme com "
        "`compras_catmat_consultar(<codigo>)` se a `descricaoItem` oficial "
        "bate com a spec.\n\n"
        "3. **Avalie as fontes**\n"
        "   - Quantos órgãos diferentes apareceram?\n"
        "   - Cobertura geográfica (UFs).\n"
        "   - Janela temporal real dos preços (preços muito antigos devem ser "
        "atualizados por índice — IPCA, IGP-M — conforme art. 6º da IN 65).\n\n"
        "4. **Estatísticas a apresentar no ETP**\n"
        "   - Valor mediano do cluster escolhido (recomendado pela IN 65 como "
        "base do valor estimado).\n"
        "   - Mínimo, máximo, média, desvio-padrão, CV.\n"
        "   - Quartis e quantos preços foram descartados como outliers (IQR).\n"
        + qtd_txt
        + "\n5. **Justificativa metodológica para o ETP**\n"
        "   - Cite IN 65/2021 art. 5º (ordem de preferência: painel de preços > "
        "contratações similares > pesquisa direta).\n"
        "   - Explique o método de tratamento estatístico usado.\n"
        "   - Justifique descartes de outliers (IQR de Tukey) e — se aplicável — "
        "o ajuste motivado de recorte amostral pelo cluster escolhido.\n\n"
        "6. **Tabela final** — colunas: órgão, modalidade, data, qtd, valor unitário, "
        "marca/modelo (se disponível), fonte (UASG). Marque os outliers descartados "
        "e indique a qual cluster cada linha pertence.\n\n"
        "7. **Recomendação de valor unitário estimado** + total (se quantidade "
        "fornecida)."
    )


@mcp.prompt(
    name="tendencia_contratacoes_periodo",
    description=(
        "Analisa tendência de contratações públicas em um intervalo, com "
        "bucketing temporal (mês/trimestre/ano) e opcionalmente comparação A vs B. "
        "Usa compras_aggregate_contratacoes_por_periodo + compras_comparar_periodos_contratacoes."
    ),
    tags={"analitico", "tendencia", "comparacao"},
)
def tendencia_contratacoes_periodo(
    data_inicial: Annotated[
        str,
        Field(description="Data inicial da janela (YYYY-MM-DD)."),
    ],
    data_final: Annotated[
        str,
        Field(description="Data final da janela (YYYY-MM-DD)."),
    ],
    codigo_modalidade: Annotated[
        int,
        Field(
            description=(
                "Modalidade PNCP. Comuns: 6=Pregão Eletrônico, 8=Dispensa, "
                "4=Concorrência Eletrônica."
            )
        ),
    ] = 6,
    uf: Annotated[
        str | None,
        Field(default=None, min_length=2, max_length=2, description="UF opcional."),
    ] = None,
    comparar_com_periodo_anterior: Annotated[
        bool,
        Field(
            default=False,
            description=(
                "Se True, compara a janela atual com a janela imediatamente "
                "anterior de mesmo tamanho (delta absoluto + percentual)."
            ),
        ),
    ] = False,
) -> str:
    uf_txt = f", uf='{uf}'" if uf else ""
    extra = ""
    if comparar_com_periodo_anterior:
        extra = (
            "\n\n3. **Comparativo período-a-período**\n"
            f"   - Use `compras_comparar_periodos_contratacoes` passando como "
            f"periodo_a a janela anterior de mesmo tamanho e como periodo_b "
            f"o intervalo {data_inicial}..{data_final}.\n"
            "   - Apresente delta absoluto e percentual de contagem.\n"
            "   - Cite o `_total_registros` retornado em cada lado."
        )
    return (
        f"Produza um relatório de tendência de contratações no período "
        f"**{data_inicial}** a **{data_final}** (modalidade {codigo_modalidade}"
        f"{uf_txt}).\n\n"
        "1. **Série temporal** — `compras_aggregate_contratacoes_por_periodo("
        f"data_inicial='{data_inicial}', data_final='{data_final}', "
        f"codigo_modalidade={codigo_modalidade}"
        + uf_txt
        + ", granularidade='mes')`.\n"
        "   - Apresente a contagem por bucket.\n"
        "   - Aponte picos e vales; sugira hipóteses (eleitoral, fim de "
        "exercício, recesso etc.).\n\n"
        "2. **Granularidade alternativa**\n"
        "   - Se a janela for >= 2 anos, refaça com `granularidade='ano'`.\n"
        "   - Se for <= 6 meses, refaça com `granularidade='semana'`.\n"
        + extra
        + "\n\n4. **Síntese** (4-6 linhas)\n"
        "   - Tendência geral (crescente / estável / decrescente).\n"
        "   - Sazonalidade observada.\n"
        "   - Recomendações para planejamento (PCA do ano seguinte)."
    )
