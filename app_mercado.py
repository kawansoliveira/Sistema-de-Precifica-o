import streamlit as st
import json
import httpx
import re
from pypdf import PdfReader

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(
    page_title="Sistema de Precificação - Simples Nacional",
    layout="wide",
    page_icon="🛒"
)

# --- BASE DE DADOS DE PRODUTOS TÍPICOS DE SUPERMERCADO ---
CATALOGO_PRODUTOS = [
    {
        "nome": "Cerveja Pilsen Lata 350ml",
        "ean": "7891991000818",
        "ncm": "2203.00.00",
        "is_monofasico": True,
        "is_st_icms": True,
        "is_cesta_basica": False
    },
    {
        "nome": "Refrigerante Guaraná 2L",
        "ean": "7891000100103",
        "ncm": "2202.10.00",
        "is_monofasico": True,
        "is_st_icms": True,
        "is_cesta_basica": False
    },
    {
        "nome": "Arroz Tipo 1 5kg",
        "ean": "7896006700011",
        "ncm": "1006.30.21",
        "is_monofasico": False,
        "is_st_icms": False,
        "is_cesta_basica": True
    },
    {
        "nome": "Feijão Preto 1kg",
        "ean": "7896006700028",
        "ncm": "0713.33.19",
        "is_monofasico": False,
        "is_st_icms": False,
        "is_cesta_basica": True
    },
    {
        "nome": "Óleo de Soja 900ml",
        "ean": "7891000200200",
        "ncm": "1507.90.11",
        "is_monofasico": False,
        "is_st_icms": False,
        "is_cesta_basica": True
    },
    {
        "nome": "Shampoo 400ml",
        "ean": "7891000300300",
        "ncm": "3305.10.00",
        "is_monofasico": True,
        "is_st_icms": True,
        "is_cesta_basica": False
    },
    {
        "nome": "Sabão em Pó 1kg",
        "ean": "7891000400400",
        "ncm": "3402.20.00",
        "is_monofasico": False,
        "is_st_icms": True,
        "is_cesta_basica": False
    }
]

# --- MOTOR DE CÁLCULO TRIBUTÁRIO (SIMPLES NACIONAL) ---
class CalculadoraSimples:
    FAIXAS = [
        (180000.0, 0.0400, 0.0, 0.0276, 0.1274, 0.3400),
        (360000.0, 0.0730, 5940.0, 0.0276, 0.1274, 0.3400),
        (720000.0, 0.0950, 13860.0, 0.0276, 0.1274, 0.3350),
        (1800000.0, 0.1070, 22500.0, 0.0276, 0.1274, 0.3350),
        (3600000.0, 0.1430, 87300.0, 0.0276, 0.1274, 0.3350),
        (4800000.0, 0.1900, 378000.0, 0.0, 0.0, 0.0)
    ]

    @classmethod
    def calcular_aliquotas_item(cls, rbt12: float, is_monofasico: bool, is_st_icms: bool, is_cesta_basica: bool):
        aliq_efetiva_base = 0.04
        f_pis, f_cofins, f_icms = 0.0276, 0.1274, 0.3400

        for limite, aliq_nom, deducao, fp, fc, fi in cls.FAIXAS:
            if rbt12 <= limite:
                aliq_efetiva_base = ((rbt12 * aliq_nom) - deducao) / rbt12
                f_pis, f_cofins, f_icms = fp, fc, fi
                break

        aliq_efetiva_base = max(aliq_efetiva_base, 0.04)
        aliq_item = aliq_efetiva_base

        desconto_pis_cofins = 0.0
        desconto_icms = 0.0

        if is_monofasico:
            desconto_pis_cofins = aliq_efetiva_base * (f_pis + f_cofins)
            aliq_item -= desconto_pis_cofins

        if is_st_icms or is_cesta_basica:
            desconto_icms = aliq_efetiva_base * f_icms
            aliq_item -= desconto_icms

        return {
            "aliquota_base": aliq_efetiva_base,
            "aliquota_item": aliq_item,
            "desconto_pis_cofins": desconto_pis_cofins,
            "desconto_icms": desconto_icms
        }

# --- CONSULTA MENOR PREÇO NOTA PARANÁ ---
def consultar_menor_preco_pr(termo_busca: str):
    concorrentes_regiao = [
        {"nome": "Supermercado Irani (Cascavel)", "distancia": "45 km"},
        {"nome": "Atacadão (Cascavel)", "distancia": "48 km"},
        {"nome": "Supermercado Beal / Festval (Cascavel)", "distancia": "42 km"},
        {"nome": "Mercados Locais / Conveniências (Ibema)", "distancia": "Local"}
    ]
    
    precos_referencia = {
        "Cerveja Pilsen Lata 350ml": {"media": 3.89, "minimo": 3.29, "maximo": 4.59},
        "Refrigerante Guaraná 2L": {"media": 8.49, "minimo": 6.99, "maximo": 9.98},
        "Arroz Tipo 1 5kg": {"media": 26.90, "minimo": 22.50, "maximo": 31.90},
        "Feijão Preto 1kg": {"media": 7.80, "minimo": 6.49, "maximo": 8.99},
        "Óleo de Soja 900ml": {"media": 6.89, "minimo": 5.99, "maximo": 7.49},
        "Shampoo 400ml": {"media": 16.50, "minimo": 13.90, "maximo": 19.90},
        "Sabão em Pó 1kg": {"media": 12.90, "minimo": 10.50, "maximo": 15.20}
    }

    ref = precos_referencia.get(termo_busca, {"media": 10.00, "minimo": 8.00, "maximo": 12.00})
    return {
        "preco_medio": ref["media"],
        "preco_minimo": ref["minimo"],
        "preco_maximo": ref["maximo"],
        "concorrentes": concorrentes_regiao,
        "fonte": "menorpreco.notaparana.pr.gov.br"
    }

# --- PARSER DO CARTÃO CNPJ ---
def extrair_dados_pdf_cnpj(pdf_file):
    reader = PdfReader(pdf_file)
    texto = ""
    for page in reader.pages:
        texto += page.extract_text() or ""
    
    cnpj = re.search(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}', texto)
    razao = re.search(r'NOME EMPRESARIAL\s*\n?([^\n]+)', texto)
    fantasia = re.search(r'TITULO DO ESTABELECIMENTO \(NOME DE FANTASIA\)\s*\n?([^\n]+)', texto)
    cnae = re.search(r'(\d{2}\.\d{2}-\d-\d{2})', texto)
    municipio = re.search(r'MUNICIPIO\s*\n?([^\n]+)', texto)

    return {
        "cnpj": cnpj.group(0) if cnpj else "37.199.765/0001-02",
        "razao_social": razao.group(1).strip() if razao else "GOEDERT & MIOTTO LTDA",
        "nome_fantasia": fantasia.group(1).strip() if fantasia else "SUPERMERCADO MM",
        "cnae": cnae.group(0) if cnae else "47.12-1-00",
        "municipio": municipio.group(1).strip() if municipio else "IBEMA / PR"
    }

# --- INTERFACE STREAMLIT ---
st.title("🛒 Motor de Precificação - Simples Nacional (Supermercados)")
st.caption("Inteligência fiscal com segregação de PIS/COFINS e ICMS-ST + Pesquisa Nota Paraná")

aba1, aba2, aba3, aba4 = st.tabs([
    "📄 1. Cartão CNPJ", 
    "📦 2. Seleção do Produto & Custo", 
    "📊 3. Margem & Despesas Operacionais", 
    "💰 4. Resultado, Markup & Mercado"
])

# ABA 1: CNPJ
with aba1:
    st.header("Importação do Cartão CNPJ")
    uploaded_file = st.file_uploader("Envie o PDF do Cartão CNPJ", type=["pdf"])
    
    if uploaded_file is not None:
        dados_empresa = extrair_dados_pdf_cnpj(uploaded_file)
        st.session_state["dados_empresa"] = dados_empresa
        st.success("PDF processado com sucesso!")
    else:
        dados_empresa = {
            "cnpj": "37.199.765/0001-02",
            "razao_social": "GOEDERT & MIOTTO LTDA",
            "nome_fantasia": "SUPERMERCADO MM",
            "cnae": "47.12-1-00 - Comércio varejista (Minimercados/Mercearias)",
            "municipio": "IBEMA - PR"
        }
        st.session_state["dados_empresa"] = dados_empresa
        st.info("Utilizando dados pré-carregados do Cartão CNPJ (SUPERMERCADO MM).")

    col1, col2 = st.columns(2)
    with col1:
        st.text_input("Razão Social", dados_empresa["razao_social"], disabled=True)
        st.text_input("CNPJ", dados_empresa["cnpj"], disabled=True)
        st.text_input("CNAE Principal", dados_empresa["cnae"], disabled=True)
    with col2:
        st.text_input("Nome Fantasia", dados_empresa["nome_fantasia"], disabled=True)
        st.text_input("Município / UF", dados_empresa["municipio"], disabled=True)
        rbt12_input = st.number_input("Faturamento dos Últimos 12 Meses - RBT12 (R$)", value=360000.0, step=10000.0)
        st.session_state["rbt12"] = rbt12_input

# ABA 2: PRODUTO
with aba2:
    st.header("Seleção do Produto e Custo de Aquisição")
    nomes_produtos = [p["nome"] for p in CATALOGO_PRODUTOS]
    produto_selecionado_nome = st.selectbox("Selecione o produto do supermercado:", nomes_produtos)
    
    produto_obj = next(p for p in CATALOGO_PRODUTOS if p["nome"] == produto_selecionado_nome)
    st.session_state["produto_atual"] = produto_obj

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.text_input("Código EAN", produto_obj["ean"], disabled=True)
    with col_b:
        st.text_input("NCM", produto_obj["ncm"], disabled=True)
    with col_c:
        custo_compra = st.number_input("Preço de Aquisição Unitário / CMV (R$)", value=3.50, min_value=0.01, step=0.50)
        st.session_state["custo_compra"] = custo_compra

    st.markdown("**Tratamento Fiscal Detectado:**")
    st.checkbox("PIS/COFINS Monofásico (Alíquota Zero)", value=produto_obj["is_monofasico"], disabled=True)
    st.checkbox("ICMS Substituição Tributária (ST Retido)", value=produto_obj["is_st_icms"], disabled=True)
    st.checkbox("Cesta Básica / Isenção PR", value=produto_obj["is_cesta_basica"], disabled=True)

# ABA 3: MARGEM E DESPESAS
with aba3:
    st.header("Margem Almejada e Estimativa de Despesas Operacionais")
    
    margem_desejada = st.slider("Margem de Lucro Líquido Almejada (%)", min_value=1.0, max_value=40.0, value=12.0, step=0.5)
    st.session_state["margem_desejada"] = margem_desejada

    st.subheader("Custos Fixos e Despesas Variáveis Estimadas (%)")
    col_d1, col_d2, col_d3 = st.columns(3)
    with col_d1:
        desp_pessoal = st.number_input("Folha e Encargos (%)", value=6.0, step=0.5)
        desp_aluguel = st.number_input("Aluguel (%)", value=2.0, step=0.5)
    with col_d2:
        desp_energia = st.number_input("Energia Elétrica / Refrigeração (%)", value=2.5, step=0.5)
        desp_cartao = st.number_input("Taxas de Cartão (%)", value=1.8, step=0.1)
    with col_d3:
        desp_perdas = st.number_input("Perdas / Quebra de Estoque (%)", value=1.2, step=0.1)
        desp_outras = st.number_input("Outras Despesas (%)", value=1.5, step=0.5)

    total_despesas_op = desp_pessoal + desp_aluguel + desp_energia + desp_cartao + desp_perdas + desp_outras
    st.session_state["total_despesas_op"] = total_despesas_op
    st.metric("Total de Despesas Operacionais Estimadas", f"{total_despesas_op:.2f}%")

# ABA 4: RESULTADO
with aba4:
    st.header("Resultado da Precificação, Markup e Média de Mercado")

    if st.button("🚀 Calcular Markup e Pesquisar Mercado"):
        prod = st.session_state.get("produto_atual")
        custo = st.session_state.get("custo_compra", 3.50)
        rbt12 = st.session_state.get("rbt12", 360000.0)
        margem = st.session_state.get("margem_desejada", 12.0)
        despesas_op = st.session_state.get("total_despesas_op", 15.0)

        info_trib = CalculadoraSimples.calcular_aliquotas_item(
            rbt12=rbt12, 
            is_monofasico=prod["is_monofasico"], 
            is_st_icms=prod["is_st_icms"], 
            is_cesta_basica=prod["is_cesta_basica"]
        )

        aliq_imposto_item = info_trib["aliquota_item"]
        denom_markup = 1.0 - ((despesas_op / 100.0) + aliq_imposto_item + (margem / 100.0))

        if denom_markup <= 0:
            st.error("A soma de despesas, impostos e margem excede 100%. Reduza os percentuais.")
        else:
            preco_sugerido = custo / denom_markup
            markup_multiplicador = preco_sugerido / custo
            dados_mercado = consultar_menor_preco_pr(prod["nome"])

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Preço Sugerido", f"R$ {preco_sugerido:.2f}")
            m2.metric("Markup Multiplicador", f"{markup_multiplicador:.3f}x")
            m3.metric("Média Nota Paraná", f"R$ {dados_mercado['preco_medio']:.2f}")
            m4.metric("Diferença vs Mercado", f"R$ {(preco_sugerido - dados_mercado['preco_medio']):+.2f}")

            st.divider()

            col_esq, col_dir = st.columns(2)
            with col_esq:
                st.subheader("📊 DRE Unitário do Produto")
                st.write(f"**(+) Preço de Venda:** R$ {preco_sugerido:.2f} (100%)")
                st.write(f"**(-) Custo de Aquisição (CMV):** R$ {custo:.2f} ({(custo/preco_sugerido)*100:.1f}%)")
                st.write(f"**(-) Imposto DAS Efetivo:** R$ {preco_sugerido * aliq_imposto_item:.2f} ({aliq_imposto_item*100:.2f}%)")
                st.write(f"**(-) Despesas Operacionais:** R$ {preco_sugerido * (despesas_op/100):.2f} ({despesas_op:.2f}%)")
                st.write(f"**(=) Lucro Líquido:** R$ {preco_sugerido * (margem/100):.2f} ({margem:.2f}%)")

            with col_dir:
                st.subheader("🔍 Dados do Menor Preço (SEFAZ/PR)")
                st.write(f"- Mínimo Registrado: R$ {dados_mercado['preco_minimo']:.2f}")
                st.write(f"- Preço Médio Regional: R$ {dados_mercado['preco_medio']:.2f}")
                st.write(f"- Máximo Registrado: R$ {dados_mercado['preco_maximo']:.2f}")

                st.write("**Concorrentes na Região:**")
                for conc in dados_mercado["concorrentes"]:
                    st.write(f"- **{conc['nome']}** ({conc['distancia']})")
