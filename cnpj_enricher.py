"""
cnpj_enricher.py - Motor de enriquecimento CNPJ e Quadro Societário (QSA)
v2.0 - Cruzamento inteligente por ENDEREÇO + NOME para máxima precisão.

ESTRATÉGIA:
1. Buscar CNPJs candidatos pelo nome no CNPJ.biz (retorna vários)
2. Para cada candidato, consultar dados completos (com endereço) via API gratuita
3. CRUZAR o endereço da Receita Federal com o endereço do Google Maps
4. Se o endereço bater → CNPJ confirmado com alta confiança
5. Extrair sócios (QSA) do CNPJ confirmado

APIs GRATUITAS USADAS (sem cadastro/token):
- OpenCNPJ (api.opencnpj.org) → Gratuita, sem auth, inclui endereço + QSA + email + telefone
- BrasilAPI (brasilapi.com.br) → Gratuita, sem auth, inclui QSA
- CNPJ.ws (publica.cnpj.ws) → Gratuita, 3 req/min, dados completos
- CNPJ.biz (cnpj.biz) → Scraping para busca textual por nome
"""
import asyncio
import random
import re
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import httpx
from playwright.async_api import async_playwright

from config import USER_AGENTS, BRASIL_API_CNPJ


# ============================================================
# UTILIDADES DE COMPARAÇÃO DE ENDEREÇO
# ============================================================

def _normalizar_endereco(endereco: str) -> str:
    """
    Normaliza um endereço para comparação.
    Remove acentos, pontuação, abreviações comuns, e padroniza.
    """
    if not endereco:
        return ""

    texto = endereco.lower().strip()

    # Remover CEP
    texto = re.sub(r'\d{5}-?\d{3}', '', texto)

    # Padronizar abreviações comuns
    substituicoes = {
        r'\bav\.?\b': 'avenida',
        r'\br\.?\b': 'rua',
        r'\bal\.?\b': 'alameda',
        r'\btv\.?\b': 'travessa',
        r'\bpç\.?\b': 'praca',
        r'\bpça\.?\b': 'praca',
        r'\bjd\.?\b': 'jardim',
        r'\bvl\.?\b': 'vila',
        r'\bconj\.?\b': 'conjunto',
        r'\blt\.?\b': 'lote',
        r'\bqd\.?\b': 'quadra',
        r'\bn[º°]\.?\b': '',
        r'\bnr\.?\b': '',
        r'\bpres\.?\b': 'presidente',
        r'\bdes\.?\b': 'desembargador',
        r'\bcel\.?\b': 'coronel',
        r'\bgen\.?\b': 'general',
        r'\bdr\.?\b': 'doutor',
        r'\bprof\.?\b': 'professor',
        r'\bsta\.?\b': 'santa',
        r'\bsto\.?\b': 'santo',
        r'\bs/n\b': '',
    }

    for pattern, replacement in substituicoes.items():
        texto = re.sub(pattern, replacement, texto)

    # Remover caracteres especiais (manter espaços e números)
    texto = re.sub(r'[^\w\s]', ' ', texto)

    # Remover palavras muito comuns que não ajudam
    stopwords = {'de', 'do', 'da', 'dos', 'das', 'em', 'no', 'na', 'nos', 'nas',
                 'e', 'ou', 'o', 'a', 'os', 'as', 'um', 'uma', 'para', 'por',
                 'com', 'sem', 'br', 'brasil'}
    palavras = [p for p in texto.split() if p not in stopwords and len(p) > 1]

    return ' '.join(palavras)


def _extrair_numero(endereco: str) -> str:
    """Extrai o número do endereço."""
    if not endereco:
        return ""
    match = re.search(r',?\s*(?:n[º°]?\s*)?(\d{1,5})\b', endereco)
    return match.group(1) if match else ""


def _extrair_logradouro_principal(endereco: str) -> str:
    """Extrai o nome principal do logradouro."""
    if not endereco:
        return ""
    texto = re.split(r',|\d{3,}', endereco)[0]
    texto = re.sub(r'^(rua|avenida|alameda|travessa|praca|rodovia)\s+', '', texto.lower())
    return texto.strip()


def calcular_similaridade_endereco(endereco_maps: str, endereco_receita: str) -> float:
    """
    Calcula similaridade entre endereço do Google Maps e da Receita Federal.
    Retorna score de 0.0 a 1.0.

    Critérios:
    1. Número do endereço (40% do peso) → se bate, forte indicativo
    2. Logradouro normalizado (40% do peso)
    3. Logradouro principal (20% do peso)
    """
    if not endereco_maps or not endereco_receita:
        return 0.0

    score = 0.0

    # 1. Comparar números (40%)
    num_maps = _extrair_numero(endereco_maps)
    num_receita = _extrair_numero(endereco_receita)

    if num_maps and num_receita:
        if num_maps == num_receita:
            score += 0.40
        else:
            score += 0.0
    else:
        score += 0.10

    # 2. Comparar logradouro normalizado (40%)
    norm_maps = _normalizar_endereco(endereco_maps)
    norm_receita = _normalizar_endereco(endereco_receita)

    similaridade_texto = SequenceMatcher(None, norm_maps, norm_receita).ratio()
    score += similaridade_texto * 0.40

    # 3. Comparar logradouro principal (20%)
    logr_maps = _extrair_logradouro_principal(_normalizar_endereco(endereco_maps))
    logr_receita = _extrair_logradouro_principal(_normalizar_endereco(endereco_receita))

    if logr_maps and logr_receita:
        sim_logr = SequenceMatcher(None, logr_maps, logr_receita).ratio()
        score += sim_logr * 0.20

    return min(score, 1.0)


def calcular_similaridade_nome(nome_maps: str, nome_receita: str) -> float:
    """Calcula similaridade entre nome do Maps e nome fantasia/razão social."""
    if not nome_maps or not nome_receita:
        return 0.0

    n1 = nome_maps.lower().strip()
    n2 = nome_receita.lower().strip()

    # Remover termos jurídicos
    termos_juridicos = ['ltda', 'me', 'eireli', 's.a.', 'sa', 'epp', 'mgi', 'ss',
                        'microempresa', 'empresa individual', 'sociedade',
                        'restaurante', 'lanchonete', 'bar', 'pizzaria', 'padaria',
                        'comercio', 'alimentos', 'bebidas', 'refeicoes']
    for t in termos_juridicos:
        n2 = re.sub(rf'\b{t}\b', '', n2)

    n1 = ' '.join(n1.split())
    n2 = ' '.join(n2.split())

    sim_direta = SequenceMatcher(None, n1, n2).ratio()

    palavras_maps = set(p for p in n1.split() if len(p) > 2)
    palavras_receita = set(p for p in n2.split() if len(p) > 2)

    if palavras_maps:
        palavras_em_comum = palavras_maps & palavras_receita
        sim_palavras = len(palavras_em_comum) / len(palavras_maps)
    else:
        sim_palavras = 0.0

    return max(sim_direta, sim_palavras)


# ============================================================
# PASSO 1: BUSCAR CNPJs CANDIDATOS PELO NOME
# ============================================================

async def _buscar_candidatos_cnpj(nome: str, cidade: str) -> list:
    """
    Busca CNPJs candidatos pelo nome do restaurante no CNPJ.biz.
    Retorna lista de dicts: [{cnpj, nome_encontrado}, ...]
    """
    candidatos = []
    pw = None
    browser = None

    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1366, "height": 768},
            locale="pt-BR",
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        page = await context.new_page()

        termo = f"{nome} {cidade}"
        url = f"https://cnpj.biz/procura/{quote_plus(termo)}"

        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(2, 4))

        links = page.locator('a[href*="/cnpj/"]')
        count = await links.count()

        for i in range(min(count, 10)):
            try:
                link = links.nth(i)
                href = await link.get_attribute("href") or ""
                texto = (await link.text_content()).strip()

                cnpj_match = re.search(r'(\d{2}\.?\d{3}\.?\d{3}/?\.?\d{4}-?\d{2})', href)
                if not cnpj_match:
                    cnpj_match = re.search(r'(\d{2}\.?\d{3}\.?\d{3}/?\.?\d{4}-?\d{2})', texto)

                if cnpj_match:
                    cnpj_limpo = re.sub(r'[^\d]', '', cnpj_match.group(1))
                    if len(cnpj_limpo) == 14:
                        candidatos.append({
                            "cnpj": cnpj_limpo,
                            "nome_encontrado": texto[:100],
                        })
            except Exception:
                continue

    except Exception as e:
        print(f"[CNPJ] ⚠️ Busca de candidatos falhou: {e}")

    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

    # Remover duplicatas
    vistos = set()
    unicos = []
    for c in candidatos:
        if c["cnpj"] not in vistos:
            vistos.add(c["cnpj"])
            unicos.append(c)

    return unicos


# ============================================================
# PASSO 2: CONSULTAR DADOS COMPLETOS DO CNPJ (COM ENDEREÇO)
# ============================================================

async def _consultar_cnpj_openapi(cnpj: str) -> dict:
    """OpenCNPJ (api.opencnpj.org) - Gratuita, sem auth, com email + telefone + QSA."""
    url = f"https://api.opencnpj.org/{cnpj}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/json",
            })

            if resp.status_code == 200:
                data = resp.json()

                partes = []
                for campo in ["logradouro", "numero", "complemento", "bairro", "municipio", "uf"]:
                    if data.get(campo):
                        partes.append(str(data[campo]))
                endereco_receita = ", ".join(partes)

                socios = []
                for s in data.get("QSA", []):
                    qualif = s.get("qualificacao_socio", "")
                    socios.append({
                        "nome": s.get("nome_socio", ""),
                        "qualificacao": qualif,
                        "tipo": "PJ" if "jurídica" in qualif.lower()
                                or (s.get("cnpj_cpf_socio") and len(str(s.get("cnpj_cpf_socio", ""))) > 11)
                                else "PF",
                        "cpf_cnpj": s.get("cnpj_cpf_socio", ""),
                        "data_entrada": s.get("data_entrada_sociedade", ""),
                        "faixa_etaria": "",
                    })

                telefones = []
                for tel in data.get("telefones", []):
                    if not tel.get("is_fax") and tel.get("ddd") and tel.get("numero"):
                        telefones.append(f"({tel['ddd']}) {tel['numero']}")

                return {
                    "cnpj": cnpj,
                    "razao_social": data.get("razao_social", ""),
                    "nome_fantasia": data.get("nome_fantasia", ""),
                    "situacao_cadastral": data.get("situacao_cadastral", ""),
                    "data_abertura": data.get("data_inicio_atividade", ""),
                    "natureza_juridica": data.get("natureza_juridica", ""),
                    "capital_social": data.get("capital_social", 0),
                    "endereco_receita": endereco_receita,
                    "logradouro": data.get("logradouro", ""),
                    "numero": data.get("numero", ""),
                    "bairro": data.get("bairro", ""),
                    "municipio": data.get("municipio", ""),
                    "uf": data.get("uf", ""),
                    "cep": data.get("cep", ""),
                    "email_receita": data.get("email", ""),
                    "telefones_receita": telefones,
                    "cnae_principal": data.get("cnae_principal", ""),
                    "porte_empresa": data.get("porte_empresa", ""),
                    "simples": data.get("opcao_simples", None),
                    "mei": data.get("opcao_mei", None),
                    "socios": socios,
                    "fonte": "opencnpj",
                }

            elif resp.status_code == 429:
                print(f"[CNPJ] ⏳ Rate limit OpenCNPJ, aguardando 5s...")
                await asyncio.sleep(5)

    except Exception as e:
        print(f"[CNPJ] ⚠️ OpenCNPJ falhou: {e}")

    return {}


async def _consultar_cnpj_brasilapi(cnpj: str) -> dict:
    """Fallback: BrasilAPI."""
    url = f"{BRASIL_API_CNPJ}/{cnpj}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/json",
            })

            if resp.status_code == 200:
                data = resp.json()

                partes = []
                for campo in ["descricao_tipo_de_logradouro", "logradouro", "numero",
                              "complemento", "bairro", "municipio", "uf"]:
                    if data.get(campo):
                        partes.append(str(data[campo]))
                endereco_receita = ", ".join(partes)

                socios = []
                for s in data.get("qsa", []):
                    socios.append({
                        "nome": s.get("nome_socio", ""),
                        "qualificacao": s.get("qualificacao_socio", ""),
                        "tipo": "PJ" if s.get("cnpj_cpf_do_socio") and len(
                            str(s.get("cnpj_cpf_do_socio", ""))
                        ) > 11 else "PF",
                        "cpf_cnpj": s.get("cnpj_cpf_do_socio", ""),
                        "data_entrada": s.get("data_entrada_sociedade", ""),
                        "faixa_etaria": s.get("faixa_etaria", ""),
                    })

                return {
                    "cnpj": cnpj,
                    "razao_social": data.get("razao_social", ""),
                    "nome_fantasia": data.get("nome_fantasia", ""),
                    "situacao_cadastral": data.get("descricao_situacao_cadastral", ""),
                    "data_abertura": data.get("data_inicio_atividade", ""),
                    "natureza_juridica": data.get("natureza_juridica", ""),
                    "capital_social": data.get("capital_social", 0),
                    "endereco_receita": endereco_receita,
                    "logradouro": data.get("logradouro", ""),
                    "numero": data.get("numero", ""),
                    "bairro": data.get("bairro", ""),
                    "municipio": data.get("municipio", ""),
                    "uf": data.get("uf", ""),
                    "cep": data.get("cep", ""),
                    "email_receita": "",
                    "telefones_receita": [],
                    "cnae_principal": str(data.get("cnae_fiscal", "")),
                    "porte_empresa": data.get("porte", ""),
                    "simples": data.get("opcao_pelo_simples", None),
                    "mei": data.get("opcao_pelo_mei", None),
                    "socios": socios,
                    "fonte": "brasilapi",
                }

            elif resp.status_code == 429:
                print(f"[CNPJ] ⏳ Rate limit BrasilAPI, aguardando 60s...")
                await asyncio.sleep(60)

    except Exception as e:
        print(f"[CNPJ] ⚠️ BrasilAPI falhou: {e}")

    return {}


async def _consultar_cnpj_cnpjws(cnpj: str) -> dict:
    """Fallback 2: CNPJ.ws - 3 req/min."""
    url = f"https://publica.cnpj.ws/cnpj/{cnpj}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/json",
            })

            if resp.status_code == 200:
                data = resp.json()
                estab = data.get("estabelecimento", {})

                partes = []
                for campo in ["tipo_logradouro", "logradouro", "numero", "bairro"]:
                    if estab.get(campo):
                        partes.append(str(estab[campo]))
                cidade_info = estab.get("cidade", {})
                if cidade_info.get("nome"):
                    partes.append(cidade_info["nome"])
                estado_info = estab.get("estado", {})
                if estado_info.get("sigla"):
                    partes.append(estado_info["sigla"])
                endereco_receita = ", ".join(partes)

                socios = []
                for s in data.get("socios", []):
                    nome = s.get("nome", "")
                    if s.get("pessoa"):
                        nome = s["pessoa"].get("nome", nome)
                    socios.append({
                        "nome": nome,
                        "qualificacao": s.get("qualificacao", {}).get("descricao", ""),
                        "tipo": s.get("tipo", "PF"),
                        "cpf_cnpj": s.get("cpf_cnpj_socio", ""),
                        "data_entrada": s.get("data_entrada", ""),
                        "faixa_etaria": s.get("faixa_etaria", ""),
                    })

                tels = []
                if estab.get("ddd1") and estab.get("telefone1"):
                    tels.append(f"({estab['ddd1']}) {estab['telefone1']}")

                return {
                    "cnpj": cnpj,
                    "razao_social": data.get("razao_social", ""),
                    "nome_fantasia": estab.get("nome_fantasia", ""),
                    "situacao_cadastral": estab.get("situacao_cadastral", ""),
                    "data_abertura": estab.get("data_inicio_atividade", ""),
                    "natureza_juridica": data.get("natureza_juridica", {}).get("descricao", ""),
                    "capital_social": data.get("capital_social", 0),
                    "endereco_receita": endereco_receita,
                    "logradouro": estab.get("logradouro", ""),
                    "numero": estab.get("numero", ""),
                    "bairro": estab.get("bairro", ""),
                    "municipio": cidade_info.get("nome", ""),
                    "uf": estado_info.get("sigla", ""),
                    "cep": estab.get("cep", ""),
                    "email_receita": estab.get("email", ""),
                    "telefones_receita": tels,
                    "cnae_principal": "",
                    "porte_empresa": data.get("porte", {}).get("descricao", ""),
                    "simples": data.get("simples", {}).get("simples", None) if data.get("simples") else None,
                    "mei": data.get("simples", {}).get("mei", None) if data.get("simples") else None,
                    "socios": socios,
                    "fonte": "cnpjws",
                }

            elif resp.status_code == 429:
                print(f"[CNPJ] ⏳ Rate limit CNPJ.ws, aguardando 25s...")
                await asyncio.sleep(25)

    except Exception as e:
        print(f"[CNPJ] ⚠️ CNPJ.ws falhou: {e}")

    return {}


async def consultar_cnpj_completo(cnpj: str) -> dict:
    """Consulta CNPJ tentando: OpenCNPJ → BrasilAPI → CNPJ.ws"""
    dados = await _consultar_cnpj_openapi(cnpj)
    if dados:
        return dados

    await asyncio.sleep(random.uniform(1, 2))

    dados = await _consultar_cnpj_brasilapi(cnpj)
    if dados:
        return dados

    await asyncio.sleep(random.uniform(1, 2))

    dados = await _consultar_cnpj_cnpjws(cnpj)
    if dados:
        return dados

    return {}


# ============================================================
# PASSO 3: CRUZAMENTO INTELIGENTE (ENDEREÇO + NOME)
# ============================================================

SCORE_MINIMO_CONFIANCA = 0.45

async def encontrar_cnpj_por_cruzamento(nome_maps: str, endereco_maps: str,
                                         cidade: str) -> dict:
    """
    Pipeline completo:
    1. Busca candidatos pelo nome no CNPJ.biz
    2. Consulta dados completos de cada candidato (com endereço da Receita)
    3. Compara endereço Receita vs Google Maps
    4. Retorna o candidato com maior score

    O score combina: endereço (60%) + nome (40%)
    """
    print(f"[CNPJ] 🔍 Buscando candidatos: {nome_maps} ({cidade})...")

    candidatos = await _buscar_candidatos_cnpj(nome_maps, cidade)

    if not candidatos:
        print(f"[CNPJ] ⚠️ Nenhum candidato encontrado para: {nome_maps}")
        return {}

    print(f"[CNPJ] 📋 {len(candidatos)} candidato(s). Cruzando endereços...")

    melhor_match = None
    melhor_score = 0.0

    for i, cand in enumerate(candidatos):
        cnpj = cand["cnpj"]

        dados = await consultar_cnpj_completo(cnpj)
        if not dados:
            continue

        # Verificar mesma cidade
        municipio_receita = dados.get("municipio", "").lower()
        if cidade.lower() not in municipio_receita and municipio_receita not in cidade.lower():
            print(f"  └─ {cnpj}: cidade diferente ({dados.get('municipio', '?')}) → descartado")
            continue

        # Calcular scores
        score_endereco = calcular_similaridade_endereco(endereco_maps, dados.get("endereco_receita", ""))
        score_nome = calcular_similaridade_nome(nome_maps, dados.get("nome_fantasia", ""))
        score_razao = calcular_similaridade_nome(nome_maps, dados.get("razao_social", ""))
        score_nome_final = max(score_nome, score_razao)

        # Score combinado: endereço (60%) + nome (40%)
        score_total = (score_endereco * 0.60) + (score_nome_final * 0.40)

        confianca = "🟢 ALTA" if score_total >= 0.65 else "🟡 MÉDIA" if score_total >= 0.45 else "🔴 BAIXA"

        nome_display = dados.get("nome_fantasia") or dados.get("razao_social", "?")
        print(f"  └─ {cnpj}: {nome_display[:40]}")
        print(f"     Endereço RF: {dados.get('endereco_receita', '?')[:60]}")
        print(f"     Score: end={score_endereco:.2f} nome={score_nome_final:.2f} total={score_total:.2f} {confianca}")

        if score_total > melhor_score:
            melhor_score = score_total
            melhor_match = dados
            melhor_match["score_confianca"] = score_total

        if i < len(candidatos) - 1:
            await asyncio.sleep(random.uniform(2, 4))

    # Retornar melhor match se score suficiente
    if melhor_match and melhor_score >= SCORE_MINIMO_CONFIANCA:
        socios_pf = [s for s in melhor_match.get("socios", []) if s.get("tipo") == "PF"]
        socios_nomes = ", ".join([s["nome"] for s in socios_pf]) if socios_pf else "N/A"
        email_info = f" | Email: {melhor_match['email_receita']}" if melhor_match.get("email_receita") else ""

        print(f"[SUCESSO] ✅ MATCH CONFIRMADO (score: {melhor_score:.2f})")
        print(f"  Nome: {melhor_match['razao_social']}")
        print(f"  CNPJ: {melhor_match['cnpj']}")
        print(f"  Sócios PF: {socios_nomes}{email_info}")

        return melhor_match
    else:
        if melhor_match:
            print(f"[CNPJ] ⚠️ Melhor candidato score muito baixo ({melhor_score:.2f}). Descartado.")
        print(f"[CNPJ] ❌ CNPJ não confirmado para: {nome_maps}")
        return {}


# ============================================================
# INTERFACE PÚBLICA (usada pelo main.py)
# ============================================================

async def enriquecer_restaurante(restaurante: dict) -> dict:
    """
    Pipeline completo de enriquecimento.
    Usa cruzamento ENDEREÇO + NOME para máxima precisão.
    """
    return await encontrar_cnpj_por_cruzamento(
        nome_maps=restaurante.get("nome", ""),
        endereco_maps=restaurante.get("endereco", ""),
        cidade=restaurante.get("cidade", ""),
    )


async def enriquecer_batch(restaurantes: list, delay_entre: tuple = (5, 12)) -> list:
    """Enriquece lista de restaurantes em batch."""
    resultados = []
    total = len(restaurantes)

    for i, rest in enumerate(restaurantes):
        print(f"\n[CNPJ] ═══ Processando {i+1}/{total} ═══")

        dados = await enriquecer_restaurante(rest)
        resultados.append({
            "id": rest["id"],
            "dados_cnpj": dados,
        })

        if i < total - 1:
            wait = random.uniform(*delay_entre)
            print(f"[CNPJ] ⏳ Aguardando {wait:.1f}s...")
            await asyncio.sleep(wait)

    sucesso = sum(1 for r in resultados if r["dados_cnpj"])
    print(f"\n[CNPJ] ═══ RESUMO ═══")
    print(f"  Total processados: {total}")
    if total > 0:
        print(f"  CNPJs confirmados: {sucesso} ({sucesso/total*100:.0f}%)")
    print(f"  Sem match: {total - sucesso}")

    return resultados


# Compatibilidade com main.py existente
async def consultar_brasil_api(cnpj: str) -> dict:
    return await consultar_cnpj_completo(cnpj)

async def consultar_receitaws_fallback(cnpj: str) -> dict:
    return await _consultar_cnpj_cnpjws(cnpj)
