"""
receita_fetcher.py - Detalhamento de CNPJs via cnpj.biz + funções de banco
v3.1 - cnpj.biz (telefone proprietário) como fonte de detalhamento

ESTRATÉGIA:
1. CNPJs importados via dados abertos da Receita Federal (receita_federal.py)
2. Detalhamento via cnpj.biz: sócios, TELEFONE DO PROPRIETÁRIO, dados complementares
   - cnpj.biz contém telefone pessoal do proprietário (o ouro do sistema)
"""
import asyncio
import json
import random
import re
import sqlite3
from datetime import datetime

from playwright.async_api import async_playwright

from config import (
    DB_PATH, USER_AGENTS, normalizar_cidade,
    CNPJBIZ_URL, CNPJBIZ_DELAY_MIN, CNPJBIZ_DELAY_MAX, CNPJBIZ_TIMEOUT,
    CNPJBIZ_MAX_RETRIES, CNPJBIZ_RETRY_BACKOFF, CNPJBIZ_CONCURRENT_TABS,
    CNPJBIZ_REVEAL_WAIT, CNPJBIZ_CLOUDFLARE_PAUSE_MIN, CNPJBIZ_CLOUDFLARE_PAUSE_MAX,
)
from logger import log


# ============================================================
# BANCO DE DADOS: TABELA DE CNPJs DA RECEITA
# ============================================================

def _get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_tabela_receita():
    """Cria a tabela cnpjs_receita se não existir + migra colunas novas."""
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

                -- Contato
                email TEXT,
                telefone1 TEXT,
                telefone2 TEXT,
                telefone_proprietario TEXT,

                -- Dados societários
                capital_social REAL,
                porte TEXT,
                natureza_juridica TEXT,
                data_abertura TEXT,
                simples INTEGER,
                mei INTEGER,
                tipo_empresa TEXT,

                -- Sócios (JSON com lista completa)
                socios_json TEXT,

                -- Controle incremental
                fonte TEXT DEFAULT 'casadosdados',
                fonte_detalhamento TEXT,
                detalhado INTEGER DEFAULT 0,
                data_coleta TEXT,
                data_detalhamento TEXT,

                -- iFood (verificado antes do Maps)
                tem_ifood INTEGER DEFAULT 0,
                ifood_nome TEXT,
                ifood_url TEXT,

                -- Match com Google Maps
                restaurante_id INTEGER,
                score_match REAL,
                matched INTEGER DEFAULT 0,

                -- Multi-restaurante (sócio com 2+ CNPJs)
                multi_restaurante INTEGER DEFAULT 0
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

        # Migrar colunas novas em bancos existentes
        migracoes = [
            ("telefone_proprietario", "TEXT"),
            ("tem_ifood", "INTEGER DEFAULT 0"),
            ("ifood_nome", "TEXT"),
            ("ifood_url", "TEXT"),
            ("tipo_empresa", "TEXT"),
            ("fonte_detalhamento", "TEXT"),
            ("multi_restaurante", "INTEGER DEFAULT 0"),
            ("email_proprietario", "TEXT"),
            ("tipo_negocio", "TEXT"),
            ("data_opcao_simples", "TEXT"),
            ("data_situacao_cadastral", "TEXT"),
            ("tentativas_falha", "INTEGER DEFAULT 0"),
            ("ultima_falha", "TEXT"),
        ]
        cursor = conn.cursor()
        for coluna, tipo in migracoes:
            try:
                cursor.execute(f"ALTER TABLE cnpjs_receita ADD COLUMN {coluna} {tipo}")
            except sqlite3.OperationalError:
                pass

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
            (normalizar_cidade(cidade), uf.upper())
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


def _dados_sao_validos(dados: dict) -> bool:
    """Valida se os dados extraidos tem informacao util suficiente.
    Precisa ter pelo menos: endereco OU telefone OU email OU socios."""
    tem_endereco = bool(dados.get("logradouro", "").strip())
    tem_telefone = bool(
        dados.get("telefone1", "").strip()
        or dados.get("telefone2", "").strip()
        or dados.get("telefone_proprietario", "").strip()
    )
    tem_email = bool(dados.get("email", "").strip())
    socios_json = dados.get("socios_json", "[]")
    tem_socios = socios_json and socios_json != "[]"
    return tem_endereco or tem_telefone or tem_email or tem_socios


def atualizar_detalhes_cnpj(cnpj: str, dados: dict):
    """Atualiza um CNPJ com dados detalhados (sócios, email, endereço, tel proprietário).
    Só marca detalhado=1 se os dados tiverem informação útil."""
    # Validar: só marca como detalhado se tem dados úteis
    dados_validos = _dados_sao_validos(dados)
    if not dados_validos:
        log.warning(f"[DETALHE] ⚠️ {cnpj}: dados insuficientes (sem endereco/tel/email/socios) - NAO marcado como detalhado")

    conn = _get_connection()
    try:
        conn.execute("""
            UPDATE cnpjs_receita
            SET email = COALESCE(NULLIF(?, ''), email),
                email_proprietario = COALESCE(NULLIF(?, ''), email_proprietario),
                telefone1 = COALESCE(NULLIF(?, ''), telefone1),
                telefone2 = COALESCE(NULLIF(?, ''), telefone2),
                telefone_proprietario = COALESCE(NULLIF(?, ''), telefone_proprietario),
                capital_social = CASE WHEN ? > 0 THEN ? ELSE capital_social END,
                porte = COALESCE(NULLIF(?, ''), porte),
                natureza_juridica = COALESCE(NULLIF(?, ''), natureza_juridica),
                data_abertura = COALESCE(NULLIF(?, ''), data_abertura),
                data_opcao_simples = COALESCE(NULLIF(?, ''), data_opcao_simples),
                data_situacao_cadastral = COALESCE(NULLIF(?, ''), data_situacao_cadastral),
                simples = ?,
                mei = ?,
                tipo_empresa = COALESCE(NULLIF(?, ''), tipo_empresa),
                tipo_negocio = COALESCE(NULLIF(?, ''), tipo_negocio),
                socios_json = ?,
                logradouro = COALESCE(NULLIF(?, ''), logradouro),
                numero = COALESCE(NULLIF(?, ''), numero),
                complemento = COALESCE(NULLIF(?, ''), complemento),
                bairro = COALESCE(NULLIF(?, ''), bairro),
                cep = COALESCE(NULLIF(?, ''), cep),
                endereco_completo = COALESCE(NULLIF(?, ''), endereco_completo),
                cnae_principal = COALESCE(NULLIF(?, ''), cnae_principal),
                fonte_detalhamento = COALESCE(NULLIF(?, ''), fonte_detalhamento),
                detalhado = ?,
                data_detalhamento = ?
            WHERE cnpj = ?
        """, (
            dados.get("email", ""),
            dados.get("email_proprietario", ""),
            dados.get("telefone1", ""),
            dados.get("telefone2", ""),
            dados.get("telefone_proprietario", ""),
            dados.get("capital_social", 0),
            dados.get("capital_social", 0),
            dados.get("porte", ""),
            dados.get("natureza_juridica", ""),
            dados.get("data_abertura", ""),
            dados.get("data_opcao_simples", ""),
            dados.get("data_situacao_cadastral", ""),
            1 if dados.get("simples") else 0,
            1 if dados.get("mei") else 0,
            dados.get("tipo_empresa", ""),
            dados.get("tipo_negocio", ""),
            dados.get("socios_json", "[]"),
            dados.get("logradouro", ""),
            dados.get("numero", ""),
            dados.get("complemento", ""),
            dados.get("bairro", ""),
            dados.get("cep", ""),
            dados.get("endereco_completo", ""),
            dados.get("cnae_principal", ""),
            dados.get("fonte_detalhamento", ""),
            1 if dados_validos else 0,
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
        """, (normalizar_cidade(cidade), uf.upper())).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()



def obter_cnpjs_nao_detalhados(cidade: str, uf: str, limite: int = 0) -> list:
    """Retorna CNPJs que ainda não tiveram detalhes buscados.
    Prioriza CNPJs que falharam anteriormente (tentativas_falha > 0).
    limite=0 significa sem limite (todos os pendentes)."""
    conn = _get_connection()
    try:
        query = """
            SELECT cnpj FROM cnpjs_receita
            WHERE cidade = ? AND uf = ? AND detalhado = 0
            ORDER BY tentativas_falha DESC, data_coleta ASC
        """
        params = [normalizar_cidade(cidade), uf.upper()]
        if limite > 0:
            query += " LIMIT ?"
            params.append(limite)
        rows = conn.execute(query, params).fetchall()
        return [row["cnpj"] for row in rows]
    finally:
        conn.close()


def registrar_falha_cnpj(cnpj: str):
    """Registra uma falha de detalhamento para um CNPJ.
    Incrementa tentativas_falha e salva timestamp da ultima falha."""
    conn = _get_connection()
    try:
        conn.execute("""
            UPDATE cnpjs_receita
            SET tentativas_falha = COALESCE(tentativas_falha, 0) + 1,
                ultima_falha = ?
            WHERE cnpj = ?
        """, (datetime.now().isoformat(), cnpj))
        conn.commit()
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
            params = [normalizar_cidade(cidade), uf.upper()]

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

        com_tel_prop = conn.execute(
            f"SELECT COUNT(*) FROM cnpjs_receita {where} {'AND' if where else 'WHERE'} telefone_proprietario != '' AND telefone_proprietario IS NOT NULL",
            params
        ).fetchone()[0]

        com_ifood = conn.execute(
            f"SELECT COUNT(*) FROM cnpjs_receita {where} {'AND' if where else 'WHERE'} tem_ifood = 1",
            params
        ).fetchone()[0]

        return {
            "total": total,
            "detalhados": detalhados,
            "matched": matched,
            "com_email": com_email,
            "com_tel_proprietario": com_tel_prop,
            "com_ifood": com_ifood,
            "sem_detalhar": total - detalhados,
            "sem_match": total - matched,
        }
    finally:
        conn.close()




# ============================================================
# PASSO 2: DETALHAR CNPJs VIA cnpj.biz (TELEFONE PROPRIETÁRIO)
# ============================================================

# Constantes de resiliência v3.2
CHUNK_SIZE = 200  # CNPJs por lote antes de reiniciar browser
CHUNK_COOLDOWN_MIN = 60  # segundos entre chunks
CHUNK_COOLDOWN_MAX = 120
PERIODIC_PAUSE_EVERY = 100  # pausa a cada N sucessos
PERIODIC_PAUSE_MIN = 30
PERIODIC_PAUSE_MAX = 60
CRASH_PAUSE_MIN = 90  # pausa apos crash/bloqueio
CRASH_PAUSE_MAX = 120
MAX_DELAY_MULTIPLIER = 8.0  # limite maximo do delay_extra
CONSECUTIVE_FAIL_THRESHOLD = 5  # falhas consecutivas antes de pausa longa
CONSECUTIVE_FAIL_PAUSE_MIN = 120
CONSECUTIVE_FAIL_PAUSE_MAX = 180

# Viewports aleatorios para variar fingerprint
VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1920, "height": 1080},
    {"width": 1280, "height": 720},
]


def _dados_embaralhados(texto: str) -> bool:
    """Detecta se os dados estao embaralhados (anti-scraping do cnpj.biz).
    Verifica padroes invalidos como sequencias de consoantes sem sentido."""
    if not texto:
        return False
    # Procurar ruas/logradouros com consoantes sem sentido
    import re as _re
    # Extrair texto apos "Logradouro" ou "Endereco"
    match = _re.search(r'(?:logradouro|endere[cç]o)\s*[:\-]?\s*(.{5,60})', texto, _re.IGNORECASE)
    if not match:
        return False
    trecho = match.group(1).strip()
    # Mais de 4 consoantes consecutivas (improvavel em portugues)
    consoantes = _re.findall(r'[bcdfghjklmnpqrstvwxyz]{4,}', trecho.lower())
    if len(consoantes) >= 2:
        return True
    # Verificar se parece aleatório: muitas consoantes raras juntas
    for seq in consoantes:
        raras = sum(1 for c in seq if c in 'kwyxzqj')
        if raras >= 2:
            return True
    return False


async def _detectar_cloudflare(page) -> bool:
    """Detecta se a pagina esta mostrando challenge Cloudflare."""
    try:
        content = await page.content()
        return any(s in content for s in [
            "Just a moment", "Checking your browser",
            "cf-challenge", "challenge-platform",
        ])
    except Exception:
        return False


async def _detalhar_um_cnpj_biz(page, cnpj: str, idx: int, total: int,
                                 semaphore, resultados: dict,
                                 falhas_consecutivas: list, delay_extra: list,
                                 sucessos_contador: list, needs_restart: list):
    """Detalha UM CNPJ via cnpj.biz em uma tab individual com retry.
    needs_restart[0] = True sinaliza ao batch que browser precisa reiniciar."""
    cnpj_limpo = re.sub(r'[^\d]', '', cnpj)
    url = f"{CNPJBIZ_URL}/{cnpj_limpo}"

    for tentativa in range(CNPJBIZ_MAX_RETRIES):
        async with semaphore:
            try:
                # Aplicar delay extra se houve muitas falhas
                delay_mult = delay_extra[0]
                await asyncio.sleep(random.uniform(
                    CNPJBIZ_DELAY_MIN * delay_mult,
                    CNPJBIZ_DELAY_MAX * delay_mult
                ))

                await page.goto(url, wait_until="networkidle", timeout=CNPJBIZ_TIMEOUT)
                await asyncio.sleep(random.uniform(2, 4))

                # Detectar bloqueio Cloudflare
                if await _detectar_cloudflare(page):
                    falhas_consecutivas[0] += 1
                    pausa = random.uniform(CNPJBIZ_CLOUDFLARE_PAUSE_MIN,
                                           CNPJBIZ_CLOUDFLARE_PAUSE_MAX)
                    log.warning(f"[DETALHE] Cloudflare detectado para {cnpj_limpo} "
                          f"(tentativa {tentativa+1}/{CNPJBIZ_MAX_RETRIES}) - pausando {pausa:.0f}s")

                    # Auto-ajuste: se >=3 falhas seguidas, aumentar delays
                    if falhas_consecutivas[0] >= 3:
                        delay_extra[0] = min(delay_extra[0] * 1.5, MAX_DELAY_MULTIPLIER)
                        log.debug(f"[DETALHE] Auto-ajuste: delays multiplicados por {delay_extra[0]:.1f}x")
                        falhas_consecutivas[0] = 0

                    # Se muitas falhas consecutivas, sinalizar restart
                    if falhas_consecutivas[0] >= CONSECUTIVE_FAIL_THRESHOLD:
                        needs_restart[0] = True

                    await asyncio.sleep(pausa)
                    continue

                # Revelar dados mascarados
                try:
                    await page.evaluate("() => { try { revealAllContacts(); } catch(e) {} }")
                    await asyncio.sleep(random.uniform(
                        CNPJBIZ_REVEAL_WAIT - 1, CNPJBIZ_REVEAL_WAIT + 1
                    ))
                except Exception:
                    # Fallback: tentar clicar botoes manualmente
                    try:
                        botoes = page.locator('button:has-text("Ver"), a:has-text("Ver telefone"), a:has-text("Ver e-mail")')
                        count = await botoes.count()
                        for b in range(min(count, 5)):
                            try:
                                await botoes.nth(b).click(timeout=2000)
                                await asyncio.sleep(0.5)
                            except Exception:
                                pass
                        if count > 0:
                            await asyncio.sleep(random.uniform(2, 4))
                    except Exception:
                        pass

                # Extrair dados
                content = await page.content() or ""
                text = await page.evaluate(
                    "() => document.body ? document.body.innerText : ''"
                ) or ""

                # Detectar dados embaralhados (anti-scraping)
                if _dados_embaralhados(text):
                    log.warning(f"[DETALHE] Dados embaralhados detectados para {cnpj_limpo} - sinalizando restart")
                    needs_restart[0] = True
                    registrar_falha_cnpj(cnpj_limpo)
                    return

                dados = _extrair_dados_cnpjbiz(text, content)
                if dados:
                    dados["fonte_detalhamento"] = "cnpjbiz"
                    resultados[cnpj_limpo] = dados
                    falhas_consecutivas[0] = 0  # Reset falhas
                    sucessos_contador[0] += 1

                    tel_prop = dados.get("telefone_proprietario", "")
                    tel_info = f" | TEL PROP: {tel_prop}" if tel_prop else ""
                    end_info = f" | END: {dados.get('logradouro', '')[:20]}" if dados.get("logradouro") else ""
                    log.info(f"[DETALHE] ({idx+1}/{total}) {cnpj_limpo}{tel_info}{end_info}")

                    # Pausa periodica a cada N sucessos
                    if sucessos_contador[0] > 0 and sucessos_contador[0] % PERIODIC_PAUSE_EVERY == 0:
                        pausa = random.uniform(PERIODIC_PAUSE_MIN, PERIODIC_PAUSE_MAX)
                        log.info(f"[DETALHE] Pausa periodica ({sucessos_contador[0]} processados) - {pausa:.0f}s")
                        await asyncio.sleep(pausa)

                    return  # Sucesso, sair do loop de retry

                else:
                    log.warning(f"[DETALHE] ({idx+1}/{total}) {cnpj_limpo}: sem dados "
                          f"(tentativa {tentativa+1}/{CNPJBIZ_MAX_RETRIES})")
                    if tentativa < CNPJBIZ_MAX_RETRIES - 1:
                        backoff = CNPJBIZ_RETRY_BACKOFF[min(tentativa, len(CNPJBIZ_RETRY_BACKOFF)-1)]
                        log.info(f"[DETALHE] Retry em {backoff}s...")
                        await asyncio.sleep(backoff)

            except Exception as e:
                falhas_consecutivas[0] += 1
                erro_str = str(e)
                is_timeout = "Timeout" in erro_str or "timeout" in erro_str
                is_crash = any(s in erro_str for s in [
                    "Connection closed", "Target page", "crashed",
                    "ERR_UNEXPECTED_PROXY_AUTH", "browser has been closed",
                    "Target closed", "Session closed",
                ])

                log.warning(f"[DETALHE] ({idx+1}/{total}) {cnpj_limpo}: {e} "
                      f"(tentativa {tentativa+1}/{CNPJBIZ_MAX_RETRIES})")

                # Browser crash: sinalizar restart imediato
                if is_crash:
                    log.warning(f"[DETALHE] Browser crash detectado - sinalizando restart")
                    needs_restart[0] = True
                    registrar_falha_cnpj(cnpj_limpo)
                    return

                if tentativa < CNPJBIZ_MAX_RETRIES - 1:
                    backoff = CNPJBIZ_RETRY_BACKOFF[min(tentativa, len(CNPJBIZ_RETRY_BACKOFF)-1)]
                    if is_timeout:
                        pausa_timeout = random.uniform(backoff * 1.5, backoff * 3)
                        log.info(f"[DETALHE] Timeout - pausa de {pausa_timeout:.0f}s antes de retry...")
                        await asyncio.sleep(pausa_timeout)
                    else:
                        await asyncio.sleep(backoff)

                    # Auto-ajuste global
                    if falhas_consecutivas[0] >= 3:
                        delay_extra[0] = min(delay_extra[0] * 1.5, MAX_DELAY_MULTIPLIER)
                        log.warning(f"[DETALHE] Auto-ajuste: delays multiplicados por {delay_extra[0]:.1f}x")
                        falhas_consecutivas[0] = 0

                    # Muitas falhas consecutivas: sinalizar restart
                    if falhas_consecutivas[0] >= CONSECUTIVE_FAIL_THRESHOLD:
                        needs_restart[0] = True

    # Todas as tentativas falharam
    registrar_falha_cnpj(cnpj_limpo)
    log.error(f"[DETALHE] ({idx+1}/{total}) {cnpj_limpo}: FALHOU apos {CNPJBIZ_MAX_RETRIES} tentativas "
              f"(sera priorizado na proxima varredura)")


async def _criar_browser_cnpjbiz(pw):
    """Cria browser + context com fingerprint aleatorio para cnpj.biz."""
    browser = await pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    )
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport=random.choice(VIEWPORTS),
        locale="pt-BR",
    )
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    """)

    # Resolver Cloudflare com pagina inicial
    page_init = await context.new_page()
    try:
        await page_init.goto(CNPJBIZ_URL, wait_until="networkidle", timeout=CNPJBIZ_TIMEOUT)
        await asyncio.sleep(random.uniform(3, 6))
        if await _detectar_cloudflare(page_init):
            log.warning("[DETALHE] Cloudflare nao resolvido, aguardando 15s...")
            await asyncio.sleep(15)
    except Exception as e:
        log.warning(f"[DETALHE] Erro ao resolver Cloudflare: {e}")
    finally:
        try:
            await page_init.close()
        except Exception:
            pass

    return browser, context


async def _fechar_browser_seguro(browser, pw_instance=None):
    """Fecha browser e playwright de forma segura."""
    try:
        if browser:
            await browser.close()
    except Exception:
        pass
    try:
        if pw_instance:
            await pw_instance.stop()
    except Exception:
        pass


async def _processar_chunk_cnpjbiz(pw, cnpjs_chunk: list, offset: int,
                                     total_geral: int, resultados: dict,
                                     delay_extra: list) -> bool:
    """Processa um chunk de CNPJs. Retorna True se completou sem crash fatal."""
    browser = None
    try:
        browser, context = await _criar_browser_cnpjbiz(pw)
        num_tabs = min(CNPJBIZ_CONCURRENT_TABS, len(cnpjs_chunk))
        pages = []
        for _ in range(num_tabs):
            p = await context.new_page()
            pages.append(p)

        semaphore = asyncio.Semaphore(num_tabs)
        falhas_consecutivas = [0]
        sucessos_contador = [0]
        needs_restart = [False]

        # Processar sequencialmente em sub-batches para detectar restart
        i = 0
        while i < len(cnpjs_chunk):
            if needs_restart[0]:
                log.warning(f"[DETALHE] Restart sinalizado no CNPJ {i+offset+1}/{total_geral} - reiniciando browser...")
                break

            # Processar ate CONCURRENT_TABS de cada vez
            batch_end = min(i + num_tabs, len(cnpjs_chunk))
            tarefas = []
            for j in range(i, batch_end):
                cnpj = cnpjs_chunk[j]
                page = pages[j % num_tabs]
                tarefa = asyncio.create_task(
                    _detalhar_um_cnpj_biz(
                        page, cnpj, offset + j, total_geral,
                        semaphore, resultados,
                        falhas_consecutivas, delay_extra,
                        sucessos_contador, needs_restart
                    )
                )
                tarefas.append(tarefa)

            await asyncio.gather(*tarefas, return_exceptions=True)
            i = batch_end

        # Fechar tabs
        for p in pages:
            try:
                await p.close()
            except Exception:
                pass

        # Retornar posicao onde parou
        if needs_restart[0]:
            await _fechar_browser_seguro(browser)
            return i  # Posicao onde parou
        return len(cnpjs_chunk)  # Completou tudo

    except Exception as e:
        log.warning(f"[DETALHE] Chunk falhou: {e}")
        return 0
    finally:
        try:
            if browser:
                await browser.close()
        except Exception:
            pass


async def _detalhar_cnpjbiz_batch(cnpjs: list) -> dict:
    """
    Detalha CNPJs via cnpj.biz com chunking, browser restart e cool-down.
    Processa em lotes de CHUNK_SIZE CNPJs, reiniciando o browser entre lotes.
    v3.2: Resiliencia melhorada com deteccao de crash e dados embaralhados.

    Returns:
        dict[cnpj] -> dict com dados extraidos
    """
    resultados = {}
    total = len(cnpjs)

    if total == 0:
        return resultados

    num_chunks = (total + CHUNK_SIZE - 1) // CHUNK_SIZE
    log.info(f"[DETALHE] cnpj.biz: {total} CNPJs em {num_chunks} lotes de {CHUNK_SIZE}")
    log.info(f"[DETALHE] Config: {CNPJBIZ_CONCURRENT_TABS} tabs | "
          f"{CNPJBIZ_MAX_RETRIES} retries | backoff {CNPJBIZ_RETRY_BACKOFF}s | "
          f"delay {CNPJBIZ_DELAY_MIN}-{CNPJBIZ_DELAY_MAX}s")

    pw = None
    delay_extra = [1.0]

    try:
        pw = await async_playwright().start()
        processados = 0

        while processados < total:
            chunk_num = processados // CHUNK_SIZE + 1
            chunk_end = min(processados + CHUNK_SIZE, total)
            cnpjs_chunk = cnpjs[processados:chunk_end]

            log.info(f"[DETALHE] === Lote {chunk_num}/{num_chunks}: "
                     f"CNPJs {processados+1}-{chunk_end}/{total} ===")

            # Processar chunk (com restart interno se necessario)
            pos_final = await _processar_chunk_cnpjbiz(
                pw, cnpjs_chunk, processados, total, resultados, delay_extra
            )

            if pos_final < len(cnpjs_chunk) and pos_final > 0:
                # Crash no meio do chunk - avançar ate onde processou
                processados += pos_final
                log.warning(f"[DETALHE] Chunk interrompido na posicao {pos_final}. "
                           f"Pausa longa antes de reiniciar...")
                pausa = random.uniform(CRASH_PAUSE_MIN, CRASH_PAUSE_MAX)
                log.info(f"[DETALHE] Aguardando {pausa:.0f}s antes de reiniciar browser...")
                await asyncio.sleep(pausa)
            elif pos_final == 0:
                # Falha total do chunk - pular e tentar proximo
                processados += len(cnpjs_chunk)
                log.warning(f"[DETALHE] Chunk falhou completamente - pulando para proximo lote")
                pausa = random.uniform(CRASH_PAUSE_MIN, CRASH_PAUSE_MAX)
                await asyncio.sleep(pausa)
            else:
                # Chunk completo
                processados += len(cnpjs_chunk)

            # Cool-down entre chunks (exceto no ultimo)
            if processados < total:
                pausa = random.uniform(CHUNK_COOLDOWN_MIN, CHUNK_COOLDOWN_MAX)
                log.info(f"[DETALHE] Cool-down entre lotes: {pausa:.0f}s "
                         f"(processados: {len(resultados)}/{total})")
                await asyncio.sleep(pausa)

    except Exception as e:
        log.warning(f"[DETALHE] cnpj.biz batch falhou: {e}")

    finally:
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass

    return resultados


def _extrair_dados_cnpjbiz(text: str, html: str) -> dict:
    """
    Extrai dados estruturados do texto da página cnpj.biz.
    Usa regex para extrair campos de texto, independente de CSS/HTML.
    """
    if not text or len(text) < 100:
        return {}

    # Detectar CNPJ inexistente no cnpj.biz
    if "não está mais no nosso banco" in text or "Alguma coisa saiu errado" in text:
        return {}

    dados = {
        "logradouro": "",
        "numero": "",
        "complemento": "",
        "bairro": "",
        "cep": "",
        "endereco_completo": "",
        "email": "",
        "email_proprietario": "",
        "telefone1": "",
        "telefone2": "",
        "telefone_proprietario": "",
        "capital_social": 0,
        "porte": "",
        "natureza_juridica": "",
        "data_abertura": "",
        "data_opcao_simples": "",
        "data_situacao_cadastral": "",
        "simples": 0,
        "mei": 0,
        "tipo_empresa": "",
        "tipo_negocio": "",
        "socios_json": "[]",
        "socios": [],
        "cnae_principal": "",
    }

    lines = text.split('\n')
    lines = [l.strip() for l in lines if l.strip()]

    def find_value(label_pattern, lines_list=lines):
        """Busca valor após um label nos textos."""
        # Envolver padrões com | em grupo não-capturante para evitar bug de grupo None
        safe_pattern = f'(?:{label_pattern})'
        for j, line in enumerate(lines_list):
            if re.search(safe_pattern, line, re.IGNORECASE):
                # Valor pode estar na mesma linha ou na próxima
                match = re.search(safe_pattern + r'\s*[:\-]?\s*(.+)', line, re.IGNORECASE)
                if match and match.group(1) and match.group(1).strip():
                    return match.group(1).strip()
                if j + 1 < len(lines_list):
                    return lines_list[j + 1].strip()
        return ""

    # Natureza Jurídica (ex: "213-5 - Empresário Individual")
    nj = find_value(r'natureza\s+jur[ií]dica')
    if nj:
        dados["natureza_juridica"] = nj
        # Detectar tipo empresa a partir da natureza jurídica
        nj_lower = nj.lower()
        if "individual" in nj_lower and "responsabilidade" not in nj_lower:
            dados["tipo_empresa"] = "EI"
        elif "eireli" in nj_lower:
            dados["tipo_empresa"] = "EIRELI"
        elif "limitada" in nj_lower:
            dados["tipo_empresa"] = "LTDA"
        elif "sociedade an" in nj_lower:
            dados["tipo_empresa"] = "SA"
        elif "mei" in nj_lower or "microempreendedor" in nj_lower:
            dados["tipo_empresa"] = "MEI"
        elif "slu" in nj_lower or "responsabilidade limitada" in nj_lower:
            dados["tipo_empresa"] = "SLU"

    # Capital Social
    cs = find_value(r'capital\s+social')
    if cs:
        cs_num = re.sub(r'[^\d,\.]', '', cs)
        cs_num = cs_num.replace('.', '').replace(',', '.')
        try:
            dados["capital_social"] = float(cs_num) if cs_num else 0
        except ValueError:
            pass

    # Data de Abertura
    da = find_value(r'data\s+(?:de\s+)?abertura|in[ií]cio\s+(?:das\s+)?atividade')
    if da:
        dados["data_abertura"] = da

    # Porte
    porte = find_value(r'porte\s+(?:da\s+)?empresa|porte')
    if porte and len(porte) < 50:
        dados["porte"] = porte

    # Email (ignorar mascarados como el****@****.com)
    email_match = re.search(r'[\w\.\-]+@[\w\.\-]+\.\w{2,}', text, re.IGNORECASE)
    if email_match:
        email_found = email_match.group(0).lower()
        if '****' not in email_found and '*' not in email_found:
            dados["email"] = email_found

    # Telefones - extrair apenas com formato real (parênteses ou traço)
    # Ignorar mascarados e sequências soltas de dígitos (CNPJs, CEPs, etc)
    telefones = re.findall(r'\(\d{2}\)\s*\d{4,5}[\-\s]?\d{4}', text)
    telefones_unicos = []
    for t in telefones:
        if '****' in t or '***' in t:
            continue
        t_limpo = re.sub(r'[^\d]', '', t)
        if len(t_limpo) >= 10 and len(t_limpo) <= 11 and t_limpo not in [re.sub(r'[^\d]', '', x) for x in telefones_unicos]:
            telefones_unicos.append(t)

    if len(telefones_unicos) >= 1:
        dados["telefone1"] = telefones_unicos[0]
    if len(telefones_unicos) >= 2:
        dados["telefone2"] = telefones_unicos[1]

    # Simples Nacional / MEI (detectar ANTES de definir tel_prop)
    if re.search(r'simples\s+nacional.*?sim|optante\s+.*?simples', text, re.IGNORECASE):
        dados["simples"] = 1
    if re.search(r'mei.*?sim|microempreendedor\s+individual.*?sim', text, re.IGNORECASE):
        dados["mei"] = 1

    # Telefone do proprietário
    tel_prop = ""
    # Procurar celular (com parênteses, 9 dígitos) que NÃO é da empresa e NÃO é mascarado
    celulares = re.findall(r'\(\d{2}\)\s*9\d{4}[\-\s]?\d{4}', text)
    tels_empresa = {re.sub(r'[^\d]', '', t) for t in [dados["telefone1"], dados["telefone2"]] if t}
    for cel in celulares:
        if '****' in cel or '***' in cel:
            continue
        cel_limpo = re.sub(r'[^\d]', '', cel)
        if len(cel_limpo) == 11 and cel_limpo not in tels_empresa:
            tel_prop = cel
            break

    # Para MEI/EI: o telefone encontrado no cnpj.biz É do proprietário
    # Se não achou um celular separado, usar o telefone1 (se for celular)
    is_mei_ei = dados.get("mei") or dados.get("tipo_empresa") in ("MEI", "EI")
    if not tel_prop and is_mei_ei and dados["telefone1"]:
        if re.match(r'\(\d{2}\)\s*9', dados["telefone1"]):
            tel_prop = dados["telefone1"]

    dados["telefone_proprietario"] = tel_prop

    # Endereço
    logradouro = find_value(r'logradouro|endere[cç]o')
    if logradouro:
        dados["logradouro"] = logradouro

    numero = find_value(r'n[uú]mero')
    if numero and len(numero) < 10:
        dados["numero"] = numero

    complemento = find_value(r'complemento')
    if complemento and len(complemento) < 100:
        dados["complemento"] = complemento

    bairro = find_value(r'bairro|distrito')
    if bairro and len(bairro) < 50:
        dados["bairro"] = bairro

    # CEP: buscar especificamente após label "CEP:" para não pegar dígitos do CNPJ
    cep_val = find_value(r'CEP')
    if cep_val:
        cep_clean = re.search(r'\d{5}[\-\s]?\d{3}', cep_val)
        if cep_clean:
            dados["cep"] = cep_clean.group(0)

    # Montar endereço completo
    partes = [p for p in [dados["logradouro"], dados["numero"], dados["complemento"],
                          dados["bairro"]] if p]
    dados["endereco_completo"] = ", ".join(partes)

    # CNAE Principal - formato XX.XX-X-XX (com pontos e traços)
    cnae_match = re.search(r'(\d{2}\.\d{2}[\-]\d[/\-]\d{2})', text)
    if cnae_match:
        cnae = re.sub(r'[^\d]', '', cnae_match.group(1))
        if len(cnae) == 7:
            dados["cnae_principal"] = cnae

    # Data de abertura (formato DD/MM/YYYY, pegar só a data)
    da = dados.get("data_abertura", "")
    if da:
        da_match = re.search(r'(\d{2}/\d{2}/\d{4})', da)
        if da_match:
            dados["data_abertura"] = da_match.group(1)

    # Data opção Simples Nacional
    ds = find_value(r'data\s+op[çc][aã]o\s+(?:\-\s*)?(?:exclus[aã]o\s+)?simples')
    if ds:
        ds_match = re.search(r'(\d{2}/\d{2}/\d{4})', ds)
        if ds_match:
            dados["data_opcao_simples"] = ds_match.group(1)

    # Data Situação Cadastral
    dsc = find_value(r'data\s+situa[çc][aã]o\s+cadastral')
    if dsc:
        dsc_match = re.search(r'(\d{2}/\d{2}/\d{4})', dsc)
        if dsc_match:
            dados["data_situacao_cadastral"] = dsc_match.group(1)

    # Email do proprietário = email encontrado no cnpj.biz (é o email do dono)
    dados["email_proprietario"] = dados.get("email", "")

    # Tipo de negócio baseado no CNAE (não na forma jurídica)
    cnae = dados.get("cnae_principal", "")
    dados["tipo_negocio"] = _cnae_para_tipo_negocio(cnae)

    # Sócios (QSA) - extrair da seção de sócios
    socios = _extrair_socios_cnpjbiz(text)

    # MEI/EI sem sócios: extrair nome do proprietário da razão social
    if not socios and (dados.get("mei") or dados.get("tipo_empresa") in ("MEI", "EI")):
        razao = ""
        for line in lines:
            if re.search(r'raz[aã]o\s+social', line, re.IGNORECASE):
                match = re.search(r'raz[aã]o\s+social\s*[:\-]?\s*(.+)', line, re.IGNORECASE)
                if match:
                    razao = match.group(1).strip()
                break
        if razao:
            # Remover números e prefixos tipo "72.979.073" da razão social
            nome_prop = re.sub(r'^[\d\.\-/\s]+', '', razao).strip()
            # Remover números soltos no final (CPF parcial)
            nome_prop = re.sub(r'\s+\d{5,}$', '', nome_prop).strip()
            if nome_prop and len(nome_prop) >= 5:
                socios.append({
                    "nome": nome_prop.upper(),
                    "qualificacao": "Empresário/Proprietário",
                    "tipo": "PF",
                    "cpf_cnpj": "",
                    "data_entrada": "",
                })

    dados["socios"] = socios
    dados["socios_json"] = json.dumps(socios, ensure_ascii=False)

    return dados


# Mapeamento CNAE → tipo de negócio
CNAE_TIPO_NEGOCIO = {
    "5611201": "Restaurante",
    "5611202": "Bar",
    "5611203": "Lanchonete",
    "5612100": "Ambulante de Alimentação",
    "5620101": "Fornecimento de Refeições",
    "5620104": "Fornecimento de Alimentos para Consumo",
}


def _cnae_para_tipo_negocio(cnae: str) -> str:
    """Converte código CNAE para tipo de negócio legível."""
    if not cnae:
        return ""
    return CNAE_TIPO_NEGOCIO.get(cnae, "")


def _extrair_socios_cnpjbiz(text: str) -> list:
    """Extrai sócios da página cnpj.biz."""
    socios = []

    # Encontrar seção de sócios/QSA
    qsa_match = re.search(
        r'(?:quadro\s+(?:de\s+)?s[oó]cios|QSA)',
        text, re.IGNORECASE
    )
    if not qsa_match:
        return socios

    # Texto a partir da seção de sócios (limitar para não pegar lixo)
    texto_socios = text[qsa_match.start():]
    # Cortar na próxima seção
    fim_secao = re.search(r'\n(?:Sobre|FAQ|Atividades|Qualifica[çc][aã]o do respons[aá]vel)', texto_socios[50:])
    if fim_secao:
        texto_socios = texto_socios[:50 + fim_secao.start()]

    # Processar LINHA A LINHA para evitar captura de headers
    linhas = texto_socios.split('\n')
    palavras_proibidas = {'quadro', 'socios', 'sócios', 'administradores', 'qsa', 'sobre', 'faq'}

    for linha in linhas:
        linha = linha.strip()
        if not linha or len(linha) < 5:
            continue

        # Padrão: "Nome Completo - Sócio-Administrador" (Title Case ou UPPERCASE)
        match = re.match(
            r'^([A-ZÀ-Ú][A-Za-zÀ-ú\s]{4,60}?)\s*[\-–]\s*(.+)$',
            linha
        )
        if match:
            nome = match.group(1).strip()
            qualif = match.group(2).strip()

            # Filtrar headers e lixo
            nome_lower_words = set(nome.lower().split())
            if nome_lower_words & palavras_proibidas:
                continue
            if len(nome.split()) < 2:
                continue

            socios.append({
                "nome": nome.upper(),
                "qualificacao": qualif,
                "tipo": "PF" if len(nome.split()) >= 2 else "PJ",
                "cpf_cnpj": "",
                "data_entrada": "",
            })

    return socios



# ============================================================
# FUNÇÕES PARA iFood VIA DADOS DA RECEITA
# ============================================================

def obter_cnpjs_sem_ifood(cidade: str, uf: str, limite: int = 0) -> list:
    """Retorna CNPJs detalhados que ainda não tiveram iFood verificado.
    Prioriza CNPJs com match no Maps (nome confirmado).
    limite=0 significa sem limite."""
    conn = _get_connection()
    try:
        query = """
            SELECT cr.cnpj, cr.razao_social, cr.nome_fantasia, cr.cidade, cr.uf,
                   cr.matched, cr.restaurante_id,
                   r.nome as nome_maps
            FROM cnpjs_receita cr
            LEFT JOIN restaurantes r ON cr.restaurante_id = r.id
            WHERE cr.cidade = ? AND cr.uf = ? AND cr.detalhado = 1 AND cr.tem_ifood = 0
            AND cr.ifood_nome IS NULL
            ORDER BY cr.matched DESC
        """
        params = [normalizar_cidade(cidade), uf.upper()]
        if limite > 0:
            query += " LIMIT ?"
            params.append(limite)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def atualizar_ifood_receita(cnpj: str, tem_ifood: bool, ifood_nome: str = "", ifood_url: str = ""):
    """Salva resultado da verificação iFood na tabela cnpjs_receita."""
    conn = _get_connection()
    try:
        conn.execute("""
            UPDATE cnpjs_receita
            SET tem_ifood = ?, ifood_nome = ?, ifood_url = ?
            WHERE cnpj = ?
        """, (1 if tem_ifood else 0, ifood_nome, ifood_url, cnpj))
        conn.commit()
    finally:
        conn.close()


# ============================================================
# PIPELINE DE DETALHAMENTO: cnpj.biz + fallback OpenCNPJ
# ============================================================

async def detalhar_cnpjs_cidade(cidade: str, uf: str, limite: int = 0) -> dict:
    """
    Busca detalhes para CNPJs que ainda não foram detalhados.
    Fonte primária: cnpj.biz (com revealAllContacts para email/tel do proprietário)

    INCREMENTAL: Só detalha os que ainda não foram detalhados.
    """
    init_tabela_receita()

    # Verificar se há CNPJs importados para esta cidade
    conn = _get_connection()
    try:
        total_cidade = conn.execute(
            "SELECT COUNT(*) FROM cnpjs_receita WHERE cidade = ? AND uf = ?",
            (normalizar_cidade(cidade), uf.upper())
        ).fetchone()[0]
    finally:
        conn.close()

    if total_cidade == 0:
        log.warning(f"[DETALHE] Nenhum CNPJ importado para {cidade}/{uf}. Execute [A] primeiro!")
        return {"detalhados": 0, "total_pendente": 0, "cnpjbiz": 0, "opencnpj": 0}

    pendentes = obter_cnpjs_nao_detalhados(cidade, uf, limite)
    total = len(pendentes)

    if total == 0:
        log.info(f"[DETALHE] Todos os {total_cidade} CNPJs de {cidade}/{uf} já foram detalhados!")
        return {"detalhados": 0, "total_pendente": 0, "cnpjbiz": 0, "opencnpj": 0}

    log.info(f"[DETALHE] 📋 {total} CNPJs pendentes de detalhamento em {cidade}/{uf}")
    log.info(f"[DETALHE] 🌐 Fonte primária: cnpj.biz ({CNPJBIZ_CONCURRENT_TABS} tabs | "
          f"{CNPJBIZ_MAX_RETRIES} retries | backoff {CNPJBIZ_RETRY_BACKOFF}s)")

    # Passo 1: cnpj.biz em batch com 7 tabs simultaneas + retry
    log.info(f"[DETALHE] ── cnpj.biz ({total} CNPJs) ──")
    resultados_biz = await _detalhar_cnpjbiz_batch(pendentes)

    sucesso_biz = 0
    falhas_biz = []

    for cnpj in pendentes:
        cnpj_limpo = re.sub(r'[^\d]', '', cnpj)
        if cnpj_limpo in resultados_biz:
            atualizar_detalhes_cnpj(cnpj_limpo, resultados_biz[cnpj_limpo])
            sucesso_biz += 1
        else:
            falhas_biz.append(cnpj_limpo)

    log.info(f"[DETALHE] 📊 cnpj.biz: {sucesso_biz}/{total} detalhados | {len(falhas_biz)} falhas")

    # CNPJs que falharam ficam como pendentes - serao priorizados na proxima varredura
    # (tentativas_falha ja foi incrementado em _detalhar_um_cnpj_biz)
    total_detalhados = sucesso_biz
    falhas_total = len(falhas_biz)

    log.info(f"[DETALHE] ═══ RESUMO DETALHAMENTO {cidade}/{uf} ═══")
    log.info(f"  cnpj.biz:    {sucesso_biz}/{total} detalhados (com tel proprietario)")
    log.info(f"  TOTAL:       {total_detalhados}/{total} detalhados")
    if falhas_total > 0:
        log.info(f"  Falhas:      {falhas_total} (timeout/bloqueio/inexistente)")
        log.info(f"  [INFO] CNPJs com falha NAO foram marcados como detalhados - "
              f"serao PRIORIZADOS na proxima varredura (tentativas_falha)")

    return {
        "detalhados": total_detalhados,
        "total_pendente": falhas_total,
        "cnpjbiz": sucesso_biz,
        "opencnpj": 0,
    }
