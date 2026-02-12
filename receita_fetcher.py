"""
receita_fetcher.py - Motor de coleta de CNPJs de restaurantes por cidade
v1.0 - Usa Casa dos Dados (pesquisa avançada) para listar TODOS os CNPJs
       de restaurantes ativos em uma cidade, com endereço da Receita Federal.

ESTRATÉGIA:
1. Busca na Casa dos Dados: CNAE de restaurante + cidade + situação ATIVA
2. Salva todos os CNPJs encontrados numa tabela local (cnpjs_receita)
3. Nas próximas execuções, compara com o que já tem → só baixa NOVOS
4. Para cada CNPJ novo, busca detalhes (sócios, email) via OpenCNPJ

CNAEs DE RESTAURANTE:
- 5611201: Restaurantes e similares
- 5611202: Bares e estabelecimentos de bebidas
- 5611203: Lanchonetes, casas de chá/suco
- 5612100: Serviços ambulantes de alimentação
- 5620101: Fornecimento de alimentos para empresas
- 5620104: Fornecimento de alimentos para consumo domiciliar (delivery)
"""
import asyncio
import json
import random
import re
import sqlite3
from datetime import datetime
from urllib.parse import quote_plus

import httpx
from playwright.async_api import async_playwright

from config import DB_PATH, USER_AGENTS


# ============================================================
# CNAEs DE RESTAURANTE / ALIMENTAÇÃO
# ============================================================

CNAES_RESTAURANTE = [
    "5611201",  # Restaurantes e similares
    "5611202",  # Bar e outros estab. especializados em servir bebidas
    "5611203",  # Lanchonetes, casas de chá, de sucos e similares
    "5612100",  # Serviços ambulantes de alimentação
    "5620101",  # Fornecimento de alimentos preparados para empresas
    "5620104",  # Fornecimento de alimentos para consumo domiciliar
]

CNAES_FORMATADOS = [
    "56.11-2-01",
    "56.11-2-02",
    "56.11-2-03",
    "56.12-1-00",
    "56.20-1-01",
    "56.20-1-04",
]


# ============================================================
# BANCO DE DADOS: TABELA DE CNPJs DA RECEITA
# ============================================================

def _get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_tabela_receita():
    """Cria a tabela cnpjs_receita se não existir."""
    conn = _get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cnpjs_receita (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cnpj TEXT UNIQUE NOT NULL,
                razao_social TEXT,
                nome_fantasia TEXT,
                situacao_cadastral TEXT,
                cnae_principal TEXT,
                
                -- Endereço (chave para cruzamento com Google Maps)
                logradouro TEXT,
                numero TEXT,
                complemento TEXT,
                bairro TEXT,
                cep TEXT,
                cidade TEXT,
                uf TEXT,
                endereco_completo TEXT,
                
                -- Contato (bônus da Receita)
                email TEXT,
                telefone1 TEXT,
                telefone2 TEXT,
                
                -- Dados societários
                capital_social REAL,
                porte TEXT,
                natureza_juridica TEXT,
                data_abertura TEXT,
                simples INTEGER,
                mei INTEGER,
                
                -- Sócios (JSON com lista completa)
                socios_json TEXT,
                
                -- Controle incremental
                fonte TEXT DEFAULT 'casadosdados',
                detalhado INTEGER DEFAULT 0,
                data_coleta TEXT,
                data_detalhamento TEXT,
                
                -- Match com Google Maps
                restaurante_id INTEGER,
                score_match REAL,
                matched INTEGER DEFAULT 0
            );
            
            CREATE INDEX IF NOT EXISTS idx_receita_cnpj ON cnpjs_receita(cnpj);
            CREATE INDEX IF NOT EXISTS idx_receita_cidade ON cnpjs_receita(cidade, uf);
            CREATE INDEX IF NOT EXISTS idx_receita_endereco ON cnpjs_receita(logradouro, numero, cidade);
            CREATE INDEX IF NOT EXISTS idx_receita_matched ON cnpjs_receita(matched);
            CREATE INDEX IF NOT EXISTS idx_receita_detalhado ON cnpjs_receita(detalhado);
            
            -- Tabela de controle de varreduras da Receita
            CREATE TABLE IF NOT EXISTS varreduras_receita (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cidade TEXT NOT NULL,
                uf TEXT NOT NULL,
                cnae TEXT NOT NULL,
                total_encontrados INTEGER DEFAULT 0,
                novos_inseridos INTEGER DEFAULT 0,
                data_varredura TEXT,
                UNIQUE(cidade, uf, cnae)
            );
        """)
        conn.commit()
    finally:
        conn.close()


def cnpj_ja_existe(cnpj: str) -> bool:
    """Verifica se um CNPJ já está no banco."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM cnpjs_receita WHERE cnpj = ?", (cnpj,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def cnpjs_existentes_cidade(cidade: str, uf: str) -> set:
    """Retorna set de CNPJs já cadastrados para uma cidade."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT cnpj FROM cnpjs_receita WHERE cidade = ? AND uf = ?",
            (cidade.upper(), uf.upper())
        ).fetchall()
        return {row["cnpj"] for row in rows}
    finally:
        conn.close()


def inserir_cnpj_receita(dados: dict) -> bool:
    """Insere um CNPJ da Receita no banco. Retorna True se inseriu (novo)."""
    conn = _get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO cnpjs_receita (
                cnpj, razao_social, nome_fantasia, situacao_cadastral,
                cnae_principal, logradouro, numero, complemento, bairro,
                cep, cidade, uf, endereco_completo, email, telefone1,
                telefone2, capital_social, porte, natureza_juridica,
                data_abertura, fonte, data_coleta
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            dados.get("cnpj", ""),
            dados.get("razao_social", ""),
            dados.get("nome_fantasia", ""),
            dados.get("situacao_cadastral", "ATIVA"),
            dados.get("cnae_principal", ""),
            dados.get("logradouro", ""),
            dados.get("numero", ""),
            dados.get("complemento", ""),
            dados.get("bairro", ""),
            dados.get("cep", ""),
            dados.get("cidade", "").upper(),
            dados.get("uf", "").upper(),
            dados.get("endereco_completo", ""),
            dados.get("email", ""),
            dados.get("telefone1", ""),
            dados.get("telefone2", ""),
            dados.get("capital_social", 0),
            dados.get("porte", ""),
            dados.get("natureza_juridica", ""),
            dados.get("data_abertura", ""),
            dados.get("fonte", "casadosdados"),
            datetime.now().isoformat(),
        ))
        conn.commit()
        return conn.total_changes > 0
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def atualizar_detalhes_cnpj(cnpj: str, dados: dict):
    """Atualiza um CNPJ com dados detalhados (sócios, email) do OpenCNPJ."""
    conn = _get_connection()
    try:
        conn.execute("""
            UPDATE cnpjs_receita
            SET email = COALESCE(NULLIF(?, ''), email),
                telefone1 = COALESCE(NULLIF(?, ''), telefone1),
                telefone2 = COALESCE(NULLIF(?, ''), telefone2),
                capital_social = CASE WHEN ? > 0 THEN ? ELSE capital_social END,
                porte = COALESCE(NULLIF(?, ''), porte),
                natureza_juridica = COALESCE(NULLIF(?, ''), natureza_juridica),
                data_abertura = COALESCE(NULLIF(?, ''), data_abertura),
                simples = ?,
                mei = ?,
                socios_json = ?,
                detalhado = 1,
                data_detalhamento = ?
            WHERE cnpj = ?
        """, (
            dados.get("email", ""),
            dados.get("telefone1", ""),
            dados.get("telefone2", ""),
            dados.get("capital_social", 0),
            dados.get("capital_social", 0),
            dados.get("porte", ""),
            dados.get("natureza_juridica", ""),
            dados.get("data_abertura", ""),
            1 if dados.get("simples") else 0,
            1 if dados.get("mei") else 0,
            dados.get("socios_json", "[]"),
            datetime.now().isoformat(),
            cnpj,
        ))
        conn.commit()
    finally:
        conn.close()


def marcar_match(cnpj: str, restaurante_id: int, score: float):
    """Marca um CNPJ como 'matched' com um restaurante do Google Maps."""
    conn = _get_connection()
    try:
        conn.execute("""
            UPDATE cnpjs_receita
            SET restaurante_id = ?, score_match = ?, matched = 1
            WHERE cnpj = ?
        """, (restaurante_id, score, cnpj))
        conn.commit()
    finally:
        conn.close()


def obter_cnpjs_cidade(cidade: str, uf: str) -> list:
    """Retorna todos os CNPJs de restaurantes de uma cidade (para cruzamento)."""
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM cnpjs_receita
            WHERE cidade = ? AND uf = ? AND situacao_cadastral = 'ATIVA'
            ORDER BY logradouro, numero
        """, (cidade.upper(), uf.upper())).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def obter_cnpjs_nao_detalhados(cidade: str, uf: str, limite: int = 100) -> list:
    """Retorna CNPJs que ainda não tiveram detalhes buscados no OpenCNPJ."""
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT cnpj FROM cnpjs_receita
            WHERE cidade = ? AND uf = ? AND detalhado = 0
            LIMIT ?
        """, (cidade.upper(), uf.upper(), limite)).fetchall()
        return [row["cnpj"] for row in rows]
    finally:
        conn.close()


def estatisticas_receita(cidade: str = None, uf: str = None) -> dict:
    """Estatísticas da base de CNPJs da Receita."""
    conn = _get_connection()
    try:
        where = ""
        params = []
        if cidade and uf:
            where = "WHERE cidade = ? AND uf = ?"
            params = [cidade.upper(), uf.upper()]

        total = conn.execute(
            f"SELECT COUNT(*) FROM cnpjs_receita {where}", params
        ).fetchone()[0]

        detalhados = conn.execute(
            f"SELECT COUNT(*) FROM cnpjs_receita {where} {'AND' if where else 'WHERE'} detalhado = 1",
            params
        ).fetchone()[0]

        matched = conn.execute(
            f"SELECT COUNT(*) FROM cnpjs_receita {where} {'AND' if where else 'WHERE'} matched = 1",
            params
        ).fetchone()[0]

        com_email = conn.execute(
            f"SELECT COUNT(*) FROM cnpjs_receita {where} {'AND' if where else 'WHERE'} email != '' AND email IS NOT NULL",
            params
        ).fetchone()[0]

        return {
            "total": total,
            "detalhados": detalhados,
            "matched": matched,
            "com_email": com_email,
            "sem_detalhar": total - detalhados,
            "sem_match": total - matched,
        }
    finally:
        conn.close()


# ============================================================
# PASSO 1: BUSCAR CNPJs NA CASA DOS DADOS (via Playwright)
# ============================================================

async def _buscar_casa_dos_dados_api(cidade: str, uf: str, cnae: str,
                                      page_num: int = 1) -> list:
    """
    Tenta a API direta da Casa dos Dados.
    POST https://api.casadosdados.com.br/v2/public/cnpj/search
    Pode retornar 403 se tiver proteção JS. Nesse caso, usa Playwright.
    """
    url = "https://api.casadosdados.com.br/v2/public/cnpj/search"

    payload = {
        "query": {
            "termo": [],
            "atividade_principal": [cnae],
            "natureza_juridica": [],
            "uf": [uf.upper()],
            "municipio": [cidade.upper()],
            "situacao_cadastral": "ATIVA",
            "cep": [],
            "ddd": [],
        },
        "range_query": {
            "data_abertura": {"lte": None, "gte": None},
            "capital_social": {"lte": None, "gte": None},
        },
        "extras": {
            "somente_mei": False,
            "excluir_mei": False,
            "com_email": False,
            "incluir_atividade_secundaria": False,
            "com_contato_telefonico": False,
            "somente_fixo": False,
            "somente_celular": False,
            "somente_matriz": False,
            "somente_filial": False,
        },
        "page": page_num,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, json=payload, headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://casadosdados.com.br",
                "Referer": "https://casadosdados.com.br/",
            })

            if resp.status_code == 200:
                data = resp.json()
                empresas = data.get("data", {}).get("cnpj", [])
                if not empresas:
                    empresas = data.get("data", [])
                return empresas if isinstance(empresas, list) else []

            elif resp.status_code == 403:
                return []  # Proteção JS ativa, precisa Playwright

    except Exception as e:
        print(f"[RECEITA] ⚠️ API Casa dos Dados falhou: {e}")

    return []


async def _buscar_casa_dos_dados_playwright(cidade: str, uf: str, cnae: str) -> list:
    """
    Fallback: Usa Playwright para acessar a pesquisa avançada da Casa dos Dados
    e extrair os resultados via interceptação de requests.
    """
    resultados = []
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

        # Interceptar respostas da API
        respostas_api = []

        async def interceptar_resposta(response):
            if "cnpj/search" in response.url and response.status == 200:
                try:
                    body = await response.json()
                    respostas_api.append(body)
                except Exception:
                    pass

        page = await context.new_page()
        page.on("response", interceptar_resposta)

        # Acessar pesquisa avançada
        url = "https://casadosdados.com.br/solucao/cnpj/pesquisa-avancada"
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(random.uniform(3, 5))

        # Preencher CNAE
        cnae_input = page.locator('input[placeholder*="CNAE"], input[placeholder*="atividade"]').first
        if await cnae_input.is_visible():
            await cnae_input.fill(cnae)
            await asyncio.sleep(1)
            # Selecionar da lista dropdown se aparecer
            dropdown = page.locator('.multiselect__element, .dropdown-item, [role="option"]').first
            if await dropdown.is_visible(timeout=3000):
                await dropdown.click()
                await asyncio.sleep(0.5)

        # Preencher UF
        uf_input = page.locator('input[placeholder*="UF"], input[placeholder*="estado"]').first
        if await uf_input.is_visible():
            await uf_input.fill(uf.upper())
            await asyncio.sleep(1)
            dropdown = page.locator('.multiselect__element, .dropdown-item, [role="option"]').first
            if await dropdown.is_visible(timeout=3000):
                await dropdown.click()
                await asyncio.sleep(0.5)

        # Preencher Município
        mun_input = page.locator('input[placeholder*="município"], input[placeholder*="cidade"]').first
        if await mun_input.is_visible():
            await mun_input.fill(cidade.upper())
            await asyncio.sleep(1)
            dropdown = page.locator('.multiselect__element, .dropdown-item, [role="option"]').first
            if await dropdown.is_visible(timeout=3000):
                await dropdown.click()
                await asyncio.sleep(0.5)

        # Clicar em Pesquisar
        btn = page.locator('button:has-text("Pesquisar"), button[type="submit"]').first
        if await btn.is_visible():
            await btn.click()
            await asyncio.sleep(5)

        # Processar respostas interceptadas
        for resp_data in respostas_api:
            empresas = resp_data.get("data", {}).get("cnpj", [])
            if not empresas:
                empresas = resp_data.get("data", [])
            if isinstance(empresas, list):
                resultados.extend(empresas)

        # Paginar (se houver mais páginas)
        page_count = 1
        while page_count < 50:  # Máximo 50 páginas (1000 resultados)
            next_btn = page.locator('button:has-text("Próxima"), a:has-text("Próxima"), .page-link:has-text("›")').first
            if await next_btn.is_visible(timeout=2000) and await next_btn.is_enabled():
                respostas_api.clear()
                await next_btn.click()
                await asyncio.sleep(random.uniform(3, 5))

                for resp_data in respostas_api:
                    empresas = resp_data.get("data", {}).get("cnpj", [])
                    if not empresas:
                        empresas = resp_data.get("data", [])
                    if isinstance(empresas, list):
                        resultados.extend(empresas)
                    else:
                        break

                page_count += 1
                if not respostas_api:
                    break
            else:
                break

    except Exception as e:
        print(f"[RECEITA] ⚠️ Playwright Casa dos Dados falhou: {e}")

    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

    return resultados


def _normalizar_resultado_casa_dados(empresa: dict, cidade: str, uf: str) -> dict:
    """Normaliza um resultado da Casa dos Dados para nosso formato."""
    cnpj_raw = empresa.get("cnpj", "")
    cnpj_limpo = re.sub(r'[^\d]', '', str(cnpj_raw))

    # Extrair endereço
    logradouro = empresa.get("logradouro", "") or ""
    tipo_logradouro = empresa.get("tipo_logradouro", "") or ""
    if tipo_logradouro and not logradouro.upper().startswith(tipo_logradouro.upper()):
        logradouro = f"{tipo_logradouro} {logradouro}"

    numero = str(empresa.get("numero", "") or "")
    complemento = empresa.get("complemento", "") or ""
    bairro = empresa.get("bairro", "") or ""
    cep = str(empresa.get("cep", "") or "")

    # Montar endereço completo
    partes = [p for p in [logradouro, numero, complemento, bairro, cidade, uf] if p]
    endereco_completo = ", ".join(partes)

    # Telefones
    tel1 = ""
    tel2 = ""
    if empresa.get("ddd_telefone_1"):
        ddd = str(empresa.get("ddd_telefone_1", ""))
        num = str(empresa.get("telefone_1", ""))
        if ddd and num:
            tel1 = f"({ddd}) {num}"
    if empresa.get("ddd_telefone_2"):
        ddd = str(empresa.get("ddd_telefone_2", ""))
        num = str(empresa.get("telefone_2", ""))
        if ddd and num:
            tel2 = f"({ddd}) {num}"

    return {
        "cnpj": cnpj_limpo,
        "razao_social": empresa.get("razao_social", "") or "",
        "nome_fantasia": empresa.get("nome_fantasia", "") or "",
        "situacao_cadastral": empresa.get("situacao_cadastral", "ATIVA") or "ATIVA",
        "cnae_principal": empresa.get("cnae_fiscal_principal", "") or empresa.get("atividade_principal", "") or "",
        "logradouro": logradouro.strip(),
        "numero": numero.strip(),
        "complemento": complemento.strip(),
        "bairro": bairro.strip(),
        "cep": cep.strip(),
        "cidade": cidade.upper(),
        "uf": uf.upper(),
        "endereco_completo": endereco_completo,
        "email": empresa.get("email", "") or "",
        "telefone1": tel1,
        "telefone2": tel2,
        "capital_social": float(empresa.get("capital_social", 0) or 0),
        "porte": empresa.get("porte", "") or "",
        "natureza_juridica": empresa.get("natureza_juridica", "") or "",
        "data_abertura": empresa.get("data_inicio_atividade", "") or empresa.get("data_abertura", "") or "",
    }


# ============================================================
# PIPELINE PRINCIPAL: COLETAR CNPJs DE UMA CIDADE
# ============================================================

async def coletar_cnpjs_cidade(cidade: str, uf: str) -> dict:
    """
    Coleta TODOS os CNPJs de restaurantes ativos em uma cidade.
    
    INCREMENTAL: Compara com o que já tem no banco e só insere novos.
    
    Returns:
        dict com estatísticas: total_encontrados, novos, ja_existiam
    """
    init_tabela_receita()

    # CNPJs que já temos
    cnpjs_existentes = cnpjs_existentes_cidade(cidade, uf)
    print(f"[RECEITA] 📊 Base atual: {len(cnpjs_existentes)} CNPJs de {cidade}/{uf}")

    total_encontrados = 0
    total_novos = 0
    total_ja_existiam = 0

    for cnae in CNAES_RESTAURANTE:
        cnae_desc = {
            "5611201": "Restaurantes",
            "5611202": "Bares",
            "5611203": "Lanchonetes",
            "5612100": "Ambulantes",
            "5620101": "Alimentação empresas",
            "5620104": "Delivery domiciliar",
        }.get(cnae, cnae)

        print(f"\n[RECEITA] 🔍 CNAE {cnae} ({cnae_desc}) em {cidade}/{uf}...")

        # Tentar API direta primeiro
        pagina = 1
        total_cnae = 0
        novos_cnae = 0

        while pagina <= 50:
            empresas = await _buscar_casa_dos_dados_api(cidade, uf, cnae, pagina)

            # Se API retornou vazio na página 1, tentar Playwright
            if not empresas and pagina == 1:
                print(f"[RECEITA] ⚠️ API bloqueada, tentando via Playwright...")
                empresas = await _buscar_casa_dos_dados_playwright(cidade, uf, cnae)
                # Playwright já retorna tudo paginado
                if empresas:
                    for emp in empresas:
                        dados = _normalizar_resultado_casa_dados(emp, cidade, uf)
                        if not dados["cnpj"] or len(dados["cnpj"]) != 14:
                            continue

                        total_cnae += 1

                        if dados["cnpj"] in cnpjs_existentes:
                            total_ja_existiam += 1
                            continue

                        if inserir_cnpj_receita(dados):
                            novos_cnae += 1
                            cnpjs_existentes.add(dados["cnpj"])

                    print(f"[RECEITA] 📋 CNAE {cnae}: {total_cnae} encontrados, {novos_cnae} novos")
                break  # Playwright já pega tudo

            if not empresas:
                break

            for emp in empresas:
                dados = _normalizar_resultado_casa_dados(emp, cidade, uf)
                if not dados["cnpj"] or len(dados["cnpj"]) != 14:
                    continue

                total_cnae += 1

                if dados["cnpj"] in cnpjs_existentes:
                    total_ja_existiam += 1
                    continue

                if inserir_cnpj_receita(dados):
                    novos_cnae += 1
                    cnpjs_existentes.add(dados["cnpj"])

            # Se recebeu menos de 20, é a última página
            if len(empresas) < 20:
                break

            pagina += 1
            await asyncio.sleep(random.uniform(2, 4))

        total_encontrados += total_cnae
        total_novos += novos_cnae
        print(f"[RECEITA] ✅ CNAE {cnae}: {total_cnae} encontrados, {novos_cnae} novos")

        # Registrar varredura
        conn = _get_connection()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO varreduras_receita
                (cidade, uf, cnae, total_encontrados, novos_inseridos, data_varredura)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (cidade.upper(), uf.upper(), cnae, total_cnae, novos_cnae,
                  datetime.now().isoformat()))
            conn.commit()
        finally:
            conn.close()

        await asyncio.sleep(random.uniform(3, 6))

    # Resumo
    stats = {
        "total_encontrados": total_encontrados,
        "novos": total_novos,
        "ja_existiam": total_ja_existiam,
        "base_total": len(cnpjs_existentes),
    }

    print(f"\n[RECEITA] ═══ RESUMO {cidade}/{uf} ═══")
    print(f"  Encontrados nesta varredura: {total_encontrados}")
    print(f"  Novos inseridos: {total_novos}")
    print(f"  Já existiam no banco: {total_ja_existiam}")
    print(f"  Base total agora: {stats['base_total']}")

    return stats


# ============================================================
# PASSO 2: DETALHAR CNPJs VIA OpenCNPJ (sócios, email)
# ============================================================

async def _detalhar_opencnpj(cnpj: str) -> dict:
    """Busca detalhes completos de um CNPJ na OpenCNPJ (gratuita)."""
    url = f"https://api.opencnpj.org/{cnpj}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/json",
            })

            if resp.status_code == 200:
                data = resp.json()

                # Extrair sócios
                socios = []
                for s in data.get("QSA", []):
                    qualif = s.get("qualificacao_socio", "")
                    socios.append({
                        "nome": s.get("nome_socio", ""),
                        "qualificacao": qualif,
                        "tipo": "PJ" if "jurídica" in qualif.lower()
                                or (s.get("cnpj_cpf_socio") and
                                    len(str(s.get("cnpj_cpf_socio", ""))) > 11)
                                else "PF",
                        "cpf_cnpj": s.get("cnpj_cpf_socio", ""),
                        "data_entrada": s.get("data_entrada_sociedade", ""),
                    })

                # Telefones
                tel1 = ""
                tel2 = ""
                tels = data.get("telefones", [])
                for i, tel in enumerate(tels):
                    if not tel.get("is_fax") and tel.get("ddd") and tel.get("numero"):
                        formatted = f"({tel['ddd']}) {tel['numero']}"
                        if i == 0:
                            tel1 = formatted
                        elif i == 1:
                            tel2 = formatted

                return {
                    "email": data.get("email", ""),
                    "telefone1": tel1,
                    "telefone2": tel2,
                    "capital_social": float(str(data.get("capital_social", "0")).replace(",", ".") or 0),
                    "porte": data.get("porte_empresa", ""),
                    "natureza_juridica": data.get("natureza_juridica", ""),
                    "data_abertura": data.get("data_inicio_atividade", ""),
                    "simples": data.get("opcao_simples"),
                    "mei": data.get("opcao_mei"),
                    "socios_json": json.dumps(socios, ensure_ascii=False),
                    "socios": socios,
                }

            elif resp.status_code == 429:
                print(f"[DETALHE] ⏳ Rate limit, aguardando 5s...")
                await asyncio.sleep(5)

    except Exception as e:
        print(f"[DETALHE] ⚠️ OpenCNPJ falhou para {cnpj}: {e}")

    return {}


async def detalhar_cnpjs_cidade(cidade: str, uf: str, limite: int = 500) -> dict:
    """
    Busca detalhes (sócios, email) para CNPJs que ainda não foram detalhados.
    Usa OpenCNPJ (50 req/s permitido, mas vamos com calma).
    
    INCREMENTAL: Só detalha os que ainda não foram detalhados.
    """
    init_tabela_receita()

    pendentes = obter_cnpjs_nao_detalhados(cidade, uf, limite)
    total = len(pendentes)

    if total == 0:
        print(f"[DETALHE] ✅ Todos os CNPJs de {cidade}/{uf} já foram detalhados!")
        return {"detalhados": 0, "total_pendente": 0}

    print(f"[DETALHE] 📋 {total} CNPJs pendentes de detalhamento em {cidade}/{uf}")

    sucesso = 0
    for i, cnpj in enumerate(pendentes):
        dados = await _detalhar_opencnpj(cnpj)

        if dados:
            atualizar_detalhes_cnpj(cnpj, dados)
            socios_pf = [s for s in dados.get("socios", []) if s.get("tipo") == "PF"]
            nomes = ", ".join([s["nome"] for s in socios_pf[:2]]) if socios_pf else "N/A"
            email = f" | {dados['email']}" if dados.get("email") else ""
            print(f"[DETALHE] ✅ ({i+1}/{total}) {cnpj}: Sócios: {nomes}{email}")
            sucesso += 1
        else:
            print(f"[DETALHE] ❌ ({i+1}/{total}) {cnpj}: sem dados")

        # Rate limiting gentil
        if (i + 1) % 10 == 0:
            await asyncio.sleep(random.uniform(2, 4))
        else:
            await asyncio.sleep(random.uniform(0.5, 1.5))

    print(f"\n[DETALHE] ═══ RESUMO ═══")
    print(f"  Detalhados com sucesso: {sucesso}/{total}")

    return {"detalhados": sucesso, "total_pendente": total - sucesso}
