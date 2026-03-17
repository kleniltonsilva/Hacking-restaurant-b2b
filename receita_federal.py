"""
receita_federal.py - Importacao de Dados Abertos da Receita Federal
v4.0 - Processa Estabelecimentos + Empresas + Simples + Socios

ESTRATEGIA:
1. Baixa tabela de Municipios (pequena) para mapear codigos -> nomes
2. Baixa arquivos Estabelecimentos{0-9}.zip (~500MB cada)
3. Processa CSV dentro do ZIP sem descompactar inteiro (streaming)
4. Filtra por CNAE + situacao ATIVA + UF + municipio
5. Insere no banco cnpjs_receita (INSERT OR UPDATE campos vazios)
6. Deleta ZIP apos processar para economizar disco
7. Processa Empresas.zip (capital social, porte, natureza juridica)
8. Processa Simples.zip (Simples Nacional, MEI)
9. Processa Socios.zip (QSA com nome, qualificacao, tipo)
10. Marca detalhado=1 para CNPJs com dados uteis da RF (sem precisar cnpj.biz)

MODOS DE BUSCA:
- Capitais: 27 capitais brasileiras
- Estado: todas as cidades de um estado
- Cidades: cidades especificas escolhidas pelo usuario
"""
import asyncio
import csv
import io
import json
import os
import sqlite3
import unicodedata
import zipfile
from datetime import datetime

import httpx
from rich.progress import (
    Progress, BarColumn, TextColumn, DownloadColumn,
    TransferSpeedColumn, TimeRemainingColumn, SpinnerColumn,
    TaskProgressColumn,
)

from config import (
    DATA_DIR, DB_PATH, CAPITAIS,
    RECEITA_FEDERAL_URL, RECEITA_FEDERAL_INDEX_URL, RECEITA_FEDERAL_DIR,
    CNAES_RESTAURANTE, NUM_ARQUIVOS_ESTABELECIMENTOS, UFS_BRASIL,
    RF_ARQUIVOS_COMPLEMENTARES, RF_PORTE_MAP, RF_NATUREZA_MAP,
    RF_QUALIFICACAO_SOCIO_MAP,
)
from logger import log

# URL base resolvida (preenchida em runtime pela funcao _resolver_url_base)
_url_base_cache = None
# Pasta mais recente resolvida (ex: "2026-01-11")
_pasta_recente_cache = None


# ============================================================
# COLUNAS DO CSV DE ESTABELECIMENTOS (0-indexed)
# ============================================================
COL_CNPJ_BASICO = 0
COL_CNPJ_ORDEM = 1
COL_CNPJ_DV = 2
COL_MATRIZ_FILIAL = 3
COL_NOME_FANTASIA = 4
COL_SITUACAO = 5
COL_DATA_SITUACAO = 6
COL_DATA_INICIO = 10
COL_CNAE = 11
COL_TIPO_LOGRADOURO = 13
COL_LOGRADOURO = 14
COL_NUMERO = 15
COL_COMPLEMENTO = 16
COL_BAIRRO = 17
COL_CEP = 18
COL_UF = 19
COL_MUNICIPIO = 20
COL_DDD1 = 21
COL_TELEFONE1 = 22
COL_DDD2 = 23
COL_TELEFONE2 = 24
COL_EMAIL = 27

# ============================================================
# COLUNAS DO CSV DE EMPRESAS (0-indexed)
# ============================================================
ECOL_CNPJ_BASICO = 0
ECOL_RAZAO_SOCIAL = 1
ECOL_NATUREZA_JURIDICA = 2
ECOL_QUALIFICACAO_RESPONSAVEL = 3
ECOL_CAPITAL_SOCIAL = 4
ECOL_PORTE = 5

# ============================================================
# COLUNAS DO CSV DE SIMPLES (0-indexed)
# ============================================================
SCOL_CNPJ_BASICO = 0
SCOL_OPCAO_SIMPLES = 1
SCOL_DATA_OPCAO_SIMPLES = 2
SCOL_DATA_EXCLUSAO_SIMPLES = 3
SCOL_OPCAO_MEI = 4
SCOL_DATA_OPCAO_MEI = 5
SCOL_DATA_EXCLUSAO_MEI = 6

# ============================================================
# COLUNAS DO CSV DE SOCIOS (0-indexed)
# ============================================================
SOCOL_CNPJ_BASICO = 0
SOCOL_TIPO = 1         # 1=PJ, 2=PF, 3=Estrangeiro
SOCOL_NOME = 2
SOCOL_CPF_CNPJ = 3
SOCOL_QUALIFICACAO = 4
SOCOL_DATA_ENTRADA = 5
SOCOL_PAIS = 6
SOCOL_REPRESENTANTE = 7
SOCOL_NOME_REPRESENTANTE = 8
SOCOL_QUALIFICACAO_REPRESENTANTE = 9
SOCOL_FAIXA_ETARIA = 10

MUNICIPIOS_CACHE = os.path.join(DATA_DIR, "municipios_rf.json")


def _get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _remover_acentos(texto: str) -> str:
    """Remove acentos de um texto."""
    nfkd = unicodedata.normalize('NFKD', texto)
    return ''.join(c for c in nfkd if not unicodedata.category(c).startswith('M'))


# ============================================================
# RESOLUCAO DE URL BASE (mirror Casa dos Dados com pastas por data)
# ============================================================

async def _resolver_url_base() -> str:
    """Descobre a pasta mais recente no mirror da Casa dos Dados.
    O mirror organiza os dados em pastas por data (ex: 2026-01-11/).
    Retorna a URL completa da pasta mais recente."""
    global _url_base_cache, _pasta_recente_cache
    if _url_base_cache:
        return _url_base_cache

    import re as _re

    log.info("[RF] Descobrindo pasta mais recente no mirror...")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(RECEITA_FEDERAL_INDEX_URL)
        resp.raise_for_status()
        html = resp.text

    # Extrair datas das pastas (formato YYYY-MM-DD)
    datas = _re.findall(r'href="(\d{4}-\d{2}-\d{2})/"', html)
    if not datas:
        # Fallback: talvez a URL ja aponte direto para os arquivos
        log.warning("[RF] Nenhuma pasta de data encontrada, usando URL base direta")
        _url_base_cache = RECEITA_FEDERAL_URL
        _pasta_recente_cache = None
        return _url_base_cache

    datas.sort(reverse=True)
    _pasta_recente_cache = datas[0]
    _url_base_cache = f"{RECEITA_FEDERAL_INDEX_URL}{_pasta_recente_cache}/"
    log.info(f"[RF] Pasta mais recente: {_pasta_recente_cache}")
    return _url_base_cache


def _obter_controle(chave: str) -> str:
    """Busca valor na tabela controle_atualizacao."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT valor FROM controle_atualizacao WHERE chave = ?", (chave,)
        ).fetchone()
        return row["valor"] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _salvar_controle(chave: str, valor: str):
    """Salva valor na tabela controle_atualizacao."""
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO controle_atualizacao (chave, valor, data_registro) VALUES (?, ?, ?)",
            (chave, valor, datetime.now().isoformat())
        )
        conn.commit()
    finally:
        conn.close()


# ============================================================
# MAPEAMENTO DE MUNICIPIOS
# ============================================================

async def _baixar_municipios():
    """Baixa e parseia o arquivo Municipios.zip da Receita Federal."""
    base_url = await _resolver_url_base()
    url = f"{base_url}Municipios.zip"
    zip_path = os.path.join(RECEITA_FEDERAL_DIR, "Municipios.zip")

    log.info("[RF] Baixando tabela de municipios...")
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        with open(zip_path, 'wb') as f:
            f.write(resp.content)

    # Parse CSV dentro do ZIP
    mapping = {}
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            with zf.open(name) as f:
                reader = csv.reader(
                    io.TextIOWrapper(f, encoding='latin-1', errors='replace'),
                    delimiter=';', quotechar='"'
                )
                for row in reader:
                    if len(row) >= 2:
                        code = row[0].strip().strip('"')
                        nome = row[1].strip().strip('"').upper()
                        if code and nome:
                            mapping[code] = nome

    # Cachear como JSON
    with open(MUNICIPIOS_CACHE, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, ensure_ascii=False)

    log.info(f"[RF] {len(mapping)} municipios carregados")
    return mapping


async def carregar_municipios(forcar=False):
    """Carrega mapeamento de municipios (baixa se necessario)."""
    if not forcar and os.path.exists(MUNICIPIOS_CACHE):
        with open(MUNICIPIOS_CACHE, encoding='utf-8') as f:
            return json.load(f)
    return await _baixar_municipios()


def _nome_para_codigos(municipios_map: dict, nome_cidade: str) -> set:
    """Retorna todos os codigos RF para um nome de cidade (sem acentos, case-insensitive)."""
    nome_norm = _remover_acentos(nome_cidade).upper().strip()
    codigos = set()
    for code, nome in municipios_map.items():
        if _remover_acentos(nome).upper().strip() == nome_norm:
            codigos.add(code)
    return codigos


def _listar_cidades_estado(municipios_map: dict, uf: str, conn=None) -> list:
    """Lista cidades de um estado que tem restaurantes no banco.
    Se conn fornecido, busca no banco. Senao retorna do mapeamento."""
    # Do mapeamento nao temos UF, entao retornamos todas as cidades unicas
    # O filtro real sera feito pelo UF no CSV
    return sorted(set(municipios_map.values()))


# ============================================================
# DOWNLOAD DE ARQUIVOS
# ============================================================

async def baixar_arquivo(indice: int, progress=None, task_id=None, forcar=False):
    """Baixa um arquivo Estabelecimentos{indice}.zip com progresso."""
    base_url = await _resolver_url_base()
    nome = f"Estabelecimentos{indice}.zip"
    url = f"{base_url}{nome}"
    path = os.path.join(RECEITA_FEDERAL_DIR, nome)

    # Verificar se ja existe (comparar tamanho via HEAD)
    if not forcar and os.path.exists(path):
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.head(url)
                tamanho_remoto = int(resp.headers.get('content-length', 0))
                tamanho_local = os.path.getsize(path)
                if tamanho_local >= tamanho_remoto > 0:
                    log.info(f"[RF] {nome} ja baixado ({tamanho_local / 1024 / 1024:.0f}MB)")
                    return path
        except Exception:
            pass

    log.info(f"[RF] Baixando {nome}...")
    async with httpx.AsyncClient(timeout=1800, follow_redirects=True) as client:
        async with client.stream('GET', url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get('content-length', 0))

            if progress and task_id is not None:
                progress.update(task_id, total=total)

            with open(path + '.tmp', 'wb') as f:
                downloaded = 0
                async for chunk in resp.aiter_bytes(chunk_size=131072):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress and task_id is not None:
                        progress.update(task_id, completed=downloaded)

    # Renomear tmp -> final (atomico)
    os.replace(path + '.tmp', path)
    log.info(f"[RF] {nome} baixado ({os.path.getsize(path) / 1024 / 1024:.0f}MB)")
    return path


# ============================================================
# PROCESSAMENTO DO CSV
# ============================================================

def _processar_zip(zip_path: str, codigos_alvo: set, ufs_alvo: set,
                   municipios_map: dict, arquivo_num: int) -> dict:
    """
    Processa um ZIP de Estabelecimentos. Filtra por CNAE+situacao+UF+municipio.
    Insere registros no banco com INSERT OR UPDATE (preenche campos vazios).

    Args:
        zip_path: caminho do ZIP
        codigos_alvo: set de codigos de municipio (None = sem filtro municipio)
        ufs_alvo: set de UFs alvo
        municipios_map: {codigo: nome_cidade}
        arquivo_num: indice do arquivo (para log)

    Returns:
        dict com estatisticas
    """
    stats = {"lidos": 0, "filtrados": 0, "inseridos": 0, "ignorados": 0}
    filtrar_municipio = codigos_alvo is not None and len(codigos_alvo) > 0
    nome_arquivo = os.path.basename(zip_path)

    conn = _get_connection()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for csv_name in zf.namelist():
                log.info(f"[RF] Processando {nome_arquivo} -> {csv_name}...")
                with zf.open(csv_name) as f:
                    reader = csv.reader(
                        io.TextIOWrapper(f, encoding='latin-1', errors='replace'),
                        delimiter=';', quotechar='"'
                    )

                    batch = []
                    for row in reader:
                        stats["lidos"] += 1

                        if len(row) < 28:
                            continue

                        # Filtros rapidos (ordem de maior selectividade)
                        cnae = row[COL_CNAE].strip('"').strip()
                        if cnae not in CNAES_RESTAURANTE:
                            continue

                        situacao = row[COL_SITUACAO].strip('"').strip()
                        if situacao != "02":
                            continue

                        uf = row[COL_UF].strip('"').strip()
                        if ufs_alvo and uf not in ufs_alvo:
                            continue

                        mun_code = row[COL_MUNICIPIO].strip('"').strip()
                        if filtrar_municipio and mun_code not in codigos_alvo:
                            continue

                        stats["filtrados"] += 1

                        # Montar CNPJ completo (14 digitos)
                        cnpj_basico = row[COL_CNPJ_BASICO].strip('"').strip()
                        cnpj_ordem = row[COL_CNPJ_ORDEM].strip('"').strip()
                        cnpj_dv = row[COL_CNPJ_DV].strip('"').strip()
                        cnpj = f"{cnpj_basico}{cnpj_ordem}{cnpj_dv}"
                        if len(cnpj) != 14:
                            continue

                        # Extrair dados
                        nome_fantasia = row[COL_NOME_FANTASIA].strip('"').strip()
                        tipo_logradouro = row[COL_TIPO_LOGRADOURO].strip('"').strip()
                        logradouro = row[COL_LOGRADOURO].strip('"').strip()
                        if tipo_logradouro and logradouro:
                            logradouro = f"{tipo_logradouro} {logradouro}"
                        numero = row[COL_NUMERO].strip('"').strip()
                        complemento = row[COL_COMPLEMENTO].strip('"').strip()
                        bairro = row[COL_BAIRRO].strip('"').strip()
                        cep = row[COL_CEP].strip('"').strip()

                        ddd1 = row[COL_DDD1].strip('"').strip()
                        tel1 = row[COL_TELEFONE1].strip('"').strip()
                        telefone1 = f"({ddd1}) {tel1}" if ddd1 and tel1 else ""

                        ddd2 = row[COL_DDD2].strip('"').strip()
                        tel2 = row[COL_TELEFONE2].strip('"').strip()
                        telefone2 = f"({ddd2}) {tel2}" if ddd2 and tel2 else ""

                        email = row[COL_EMAIL].strip('"').strip().lower() if len(row) > COL_EMAIL else ""

                        cidade = municipios_map.get(mun_code, "").upper()
                        data_inicio = row[COL_DATA_INICIO].strip('"').strip() if len(row) > COL_DATA_INICIO else ""

                        # Endereco completo
                        partes = [p for p in [logradouro, numero, complemento, bairro] if p]
                        endereco_completo = ", ".join(partes)

                        batch.append((
                            cnpj, "", nome_fantasia, "ATIVA", cnae,
                            logradouro, numero, complemento, bairro, cep,
                            cidade, uf, endereco_completo,
                            email, telefone1, telefone2,
                            0, "", "", data_inicio,
                            "dados_abertos", datetime.now().isoformat(), 0,
                        ))

                        if len(batch) >= 5000:
                            ins, ign = _inserir_batch(conn, batch)
                            stats["inseridos"] += ins
                            stats["ignorados"] += ign
                            batch = []

                            # Log de progresso
                            if stats["filtrados"] % 10000 == 0:
                                log.info(f"[RF] ... {stats['filtrados']:,} filtrados, "
                                         f"{stats['inseridos']:,} novos, "
                                         f"{stats['ignorados']:,} já existentes")

                    if batch:
                        ins, ign = _inserir_batch(conn, batch)
                        stats["inseridos"] += ins
                        stats["ignorados"] += ign
    finally:
        conn.close()

    return stats


def _inserir_batch(conn, batch: list) -> tuple:
    """Insere batch de registros com INSERT OR IGNORE (apenas novos).
    Retorna (inseridos, ignorados)."""
    antes = conn.execute("SELECT changes()").fetchone()[0]
    total_changes_antes = conn.total_changes
    conn.executemany("""
        INSERT OR IGNORE INTO cnpjs_receita (
            cnpj, razao_social, nome_fantasia, situacao_cadastral, cnae_principal,
            logradouro, numero, complemento, bairro, cep,
            cidade, uf, endereco_completo,
            email, telefone1, telefone2,
            capital_social, porte, natureza_juridica, data_abertura,
            fonte, data_coleta, detalhado
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, batch)
    conn.commit()
    inseridos = conn.total_changes - total_changes_antes
    ignorados = len(batch) - inseridos
    return inseridos, ignorados


# ============================================================
# PROCESSAMENTO DE ARQUIVOS COMPLEMENTARES (v4.0)
# ============================================================

async def baixar_arquivo_complementar(nome: str, progress=None, task_id=None,
                                        forcar=False) -> str:
    """Baixa um arquivo complementar da RF (Empresas, Simples, Socios).
    Retorna o caminho do arquivo baixado."""
    base_url = await _resolver_url_base()
    nome_zip = f"{nome}.zip"
    url = f"{base_url}{nome_zip}"
    path = os.path.join(RECEITA_FEDERAL_DIR, nome_zip)

    # Verificar se ja existe
    if not forcar and os.path.exists(path):
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.head(url)
                tamanho_remoto = int(resp.headers.get('content-length', 0))
                tamanho_local = os.path.getsize(path)
                if tamanho_local >= tamanho_remoto > 0:
                    log.info(f"[RF] {nome_zip} ja baixado ({tamanho_local / 1024 / 1024:.0f}MB)")
                    return path
        except Exception:
            pass

    log.info(f"[RF] Baixando {nome_zip}...")
    async with httpx.AsyncClient(timeout=1800, follow_redirects=True) as client:
        async with client.stream('GET', url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get('content-length', 0))

            if progress and task_id is not None:
                progress.update(task_id, total=total)

            with open(path + '.tmp', 'wb') as f:
                downloaded = 0
                async for chunk in resp.aiter_bytes(chunk_size=131072):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress and task_id is not None:
                        progress.update(task_id, completed=downloaded)

    os.replace(path + '.tmp', path)
    log.info(f"[RF] {nome_zip} baixado ({os.path.getsize(path) / 1024 / 1024:.0f}MB)")
    return path


async def _baixar_arquivos_complementar(nome: str, progress, forcar: bool) -> list:
    """Baixa arquivo(s) complementar(es) da RF.
    Tenta {nome}.zip primeiro. Se 404, tenta {nome}0.zip a {nome}9.zip.
    Retorna lista de caminhos baixados."""
    try:
        task_dl = progress.add_task(f"Baixando {nome}.zip", total=0)
        zip_path = await baixar_arquivo_complementar(nome, progress, task_dl, forcar)
        progress.remove_task(task_dl)
        return [zip_path]
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 404:
            raise
        try:
            progress.remove_task(task_dl)
        except Exception:
            pass
        log.info(f"[RF] {nome}.zip nao encontrado, tentando {nome}0-9.zip...")
        paths = []
        for i in range(10):
            try:
                task_dl_i = progress.add_task(f"Baixando {nome}{i}.zip", total=0)
                p = await baixar_arquivo_complementar(f"{nome}{i}", progress, task_dl_i, forcar)
                progress.remove_task(task_dl_i)
                paths.append(p)
            except httpx.HTTPStatusError as e2:
                try:
                    progress.remove_task(task_dl_i)
                except Exception:
                    pass
                if e2.response.status_code == 404:
                    log.info(f"[RF] {nome}{i}.zip nao encontrado, parando em {i}")
                    break
                raise
        if not paths:
            raise Exception(f"Nenhum arquivo {nome} encontrado no servidor")
        log.info(f"[RF] {len(paths)} arquivos {nome} baixados")
        return paths


def _carregar_cnpjs_basicos_existentes() -> set:
    """Retorna set de cnpj_basico (8 primeiros digitos) dos CNPJs ja em cnpjs_receita.
    Usado para filtrar Empresas/Simples/Socios (que contem TODOS os CNPJs do Brasil)."""
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT SUBSTR(cnpj, 1, 8) as cb FROM cnpjs_receita").fetchall()
        return {row["cb"] for row in rows}
    finally:
        conn.close()


def _processar_empresas_zip(zip_path: str, cnpjs_set: set) -> dict:
    """Processa Empresas.zip: atualiza razao_social, capital_social, porte, natureza_juridica.
    Filtra apenas CNPJs que ja estao em cnpjs_receita (via cnpj_basico)."""
    stats = {"lidos": 0, "atualizados": 0}
    conn = _get_connection()

    try:
        with zipfile.ZipFile(zip_path) as zf:
            for csv_name in zf.namelist():
                log.info(f"[RF] Processando Empresas -> {csv_name}...")
                with zf.open(csv_name) as f:
                    reader = csv.reader(
                        io.TextIOWrapper(f, encoding='latin-1', errors='replace'),
                        delimiter=';', quotechar='"'
                    )

                    batch = []
                    for row in reader:
                        stats["lidos"] += 1

                        if len(row) < 6:
                            continue

                        cnpj_basico = row[ECOL_CNPJ_BASICO].strip('"').strip()
                        if cnpj_basico not in cnpjs_set:
                            continue

                        razao_social = row[ECOL_RAZAO_SOCIAL].strip('"').strip()
                        natureza_cod = row[ECOL_NATUREZA_JURIDICA].strip('"').strip()
                        natureza = RF_NATUREZA_MAP.get(natureza_cod, natureza_cod)

                        capital_str = row[ECOL_CAPITAL_SOCIAL].strip('"').strip()
                        capital_str = capital_str.replace(',', '.')
                        try:
                            capital = float(capital_str) if capital_str else 0
                        except ValueError:
                            capital = 0

                        porte_cod = row[ECOL_PORTE].strip('"').strip()
                        porte = RF_PORTE_MAP.get(porte_cod, porte_cod)

                        # Tipo empresa baseado na natureza
                        tipo = ""
                        nj_lower = natureza.lower()
                        if "individual" in nj_lower and "responsabilidade" not in nj_lower:
                            tipo = "EI"
                        elif "eireli" in nj_lower:
                            tipo = "EIRELI"
                        elif "limitada" in nj_lower:
                            tipo = "LTDA"
                        elif "anônima" in nj_lower or "anonima" in nj_lower:
                            tipo = "SA"
                        elif "microempreendedor" in nj_lower:
                            tipo = "MEI"
                        elif "unipessoal" in nj_lower:
                            tipo = "SLU"

                        batch.append((
                            razao_social, natureza, capital, porte, tipo, cnpj_basico
                        ))

                        if len(batch) >= 5000:
                            updated = _atualizar_empresas_batch(conn, batch)
                            stats["atualizados"] += updated
                            batch = []

                            if stats["lidos"] % 500000 == 0:
                                log.info(f"[RF] ... Empresas: {stats['lidos']:,} lidos, "
                                         f"{stats['atualizados']:,} atualizados")

                    if batch:
                        updated = _atualizar_empresas_batch(conn, batch)
                        stats["atualizados"] += updated

    finally:
        conn.close()

    return stats


def _atualizar_empresas_batch(conn, batch: list) -> int:
    """Atualiza batch de dados de Empresas no banco."""
    total_antes = conn.total_changes
    for razao, natureza, capital, porte, tipo, cnpj_basico in batch:
        conn.execute("""
            UPDATE cnpjs_receita
            SET razao_social = COALESCE(NULLIF(?, ''), razao_social),
                natureza_juridica = COALESCE(NULLIF(?, ''), natureza_juridica),
                capital_social = CASE WHEN ? > 0 THEN ? ELSE capital_social END,
                porte = COALESCE(NULLIF(?, ''), porte),
                tipo_empresa = COALESCE(NULLIF(?, ''), tipo_empresa)
            WHERE SUBSTR(cnpj, 1, 8) = ?
        """, (razao, natureza, capital, capital, porte, tipo, cnpj_basico))
    conn.commit()
    return conn.total_changes - total_antes


def _processar_simples_zip(zip_path: str, cnpjs_set: set) -> dict:
    """Processa Simples.zip: atualiza simples, mei, data_opcao_simples."""
    stats = {"lidos": 0, "atualizados": 0}
    conn = _get_connection()

    try:
        with zipfile.ZipFile(zip_path) as zf:
            for csv_name in zf.namelist():
                log.info(f"[RF] Processando Simples -> {csv_name}...")
                with zf.open(csv_name) as f:
                    reader = csv.reader(
                        io.TextIOWrapper(f, encoding='latin-1', errors='replace'),
                        delimiter=';', quotechar='"'
                    )

                    batch = []
                    for row in reader:
                        stats["lidos"] += 1

                        if len(row) < 7:
                            continue

                        cnpj_basico = row[SCOL_CNPJ_BASICO].strip('"').strip()
                        if cnpj_basico not in cnpjs_set:
                            continue

                        opcao_simples = row[SCOL_OPCAO_SIMPLES].strip('"').strip().upper()
                        data_simples = row[SCOL_DATA_OPCAO_SIMPLES].strip('"').strip()
                        opcao_mei = row[SCOL_OPCAO_MEI].strip('"').strip().upper()

                        simples = 1 if opcao_simples == "S" else 0
                        mei = 1 if opcao_mei == "S" else 0

                        batch.append((simples, mei, data_simples, cnpj_basico))

                        if len(batch) >= 5000:
                            updated = _atualizar_simples_batch(conn, batch)
                            stats["atualizados"] += updated
                            batch = []

                            if stats["lidos"] % 500000 == 0:
                                log.info(f"[RF] ... Simples: {stats['lidos']:,} lidos, "
                                         f"{stats['atualizados']:,} atualizados")

                    if batch:
                        updated = _atualizar_simples_batch(conn, batch)
                        stats["atualizados"] += updated

    finally:
        conn.close()

    return stats


def _atualizar_simples_batch(conn, batch: list) -> int:
    """Atualiza batch de dados do Simples no banco."""
    total_antes = conn.total_changes
    for simples, mei, data_simples, cnpj_basico in batch:
        conn.execute("""
            UPDATE cnpjs_receita
            SET simples = ?, mei = ?,
                data_opcao_simples = COALESCE(NULLIF(?, ''), data_opcao_simples),
                tipo_empresa = CASE WHEN ? = 1 THEN 'MEI' ELSE tipo_empresa END
            WHERE SUBSTR(cnpj, 1, 8) = ?
        """, (simples, mei, data_simples, mei, cnpj_basico))
    conn.commit()
    return conn.total_changes - total_antes


def _processar_socios_zip(zip_paths, cnpjs_set: set) -> dict:
    """Processa Socios ZIP(s): agrupa socios por CNPJ basico e salva como JSON.
    zip_paths pode ser um caminho unico (str) ou lista de caminhos."""
    if isinstance(zip_paths, str):
        zip_paths = [zip_paths]

    stats = {"lidos": 0, "cnpjs_com_socios": 0}

    # Agrupar socios por cnpj_basico em memoria (acumula de todos os arquivos)
    socios_por_cnpj = {}

    for zip_path in zip_paths:
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for csv_name in zf.namelist():
                    log.info(f"[RF] Processando Socios -> {csv_name}...")
                    with zf.open(csv_name) as f:
                        reader = csv.reader(
                            io.TextIOWrapper(f, encoding='latin-1', errors='replace'),
                            delimiter=';', quotechar='"'
                        )

                        for row in reader:
                            stats["lidos"] += 1

                            if len(row) < 7:
                                continue

                            cnpj_basico = row[SOCOL_CNPJ_BASICO].strip('"').strip()
                            if cnpj_basico not in cnpjs_set:
                                continue

                            tipo_cod = row[SOCOL_TIPO].strip('"').strip()
                            tipo = "PF" if tipo_cod == "2" else ("PJ" if tipo_cod == "1" else "Estrangeiro")

                            nome = row[SOCOL_NOME].strip('"').strip()
                            if not nome:
                                continue

                            cpf_cnpj = row[SOCOL_CPF_CNPJ].strip('"').strip() if len(row) > SOCOL_CPF_CNPJ else ""
                            qualif_cod = row[SOCOL_QUALIFICACAO].strip('"').strip() if len(row) > SOCOL_QUALIFICACAO else ""
                            qualificacao = RF_QUALIFICACAO_SOCIO_MAP.get(qualif_cod, qualif_cod)

                            data_entrada = row[SOCOL_DATA_ENTRADA].strip('"').strip() if len(row) > SOCOL_DATA_ENTRADA else ""
                            faixa_etaria = row[SOCOL_FAIXA_ETARIA].strip('"').strip() if len(row) > SOCOL_FAIXA_ETARIA else ""

                            socio = {
                                "nome": nome.upper(),
                                "qualificacao": qualificacao,
                                "tipo": tipo,
                                "cpf_cnpj": cpf_cnpj,
                                "data_entrada": data_entrada,
                                "faixa_etaria": faixa_etaria,
                            }

                            if cnpj_basico not in socios_por_cnpj:
                                socios_por_cnpj[cnpj_basico] = []
                            socios_por_cnpj[cnpj_basico].append(socio)

                            if stats["lidos"] % 1000000 == 0:
                                log.info(f"[RF] ... Socios: {stats['lidos']:,} lidos, "
                                         f"{len(socios_por_cnpj):,} CNPJs com socios")

        except Exception as e:
            log.error(f"[RF] Erro ao ler {os.path.basename(zip_path)}: {e}")

    # Salvar socios agrupados no banco
    if socios_por_cnpj:
        log.info(f"[RF] Salvando socios de {len(socios_por_cnpj):,} CNPJs no banco...")
        conn = _get_connection()
        try:
            batch_count = 0
            for cnpj_basico, socios_lista in socios_por_cnpj.items():
                socios_json = json.dumps(socios_lista, ensure_ascii=False)
                conn.execute("""
                    UPDATE cnpjs_receita
                    SET socios_json = ?
                    WHERE SUBSTR(cnpj, 1, 8) = ? AND (socios_json IS NULL OR socios_json = '[]')
                """, (socios_json, cnpj_basico))
                batch_count += 1
                if batch_count % 5000 == 0:
                    conn.commit()
            conn.commit()
            stats["cnpjs_com_socios"] = len(socios_por_cnpj)
        finally:
            conn.close()

    return stats


def _marcar_enriquecido_rf():
    """Marca detalhado=1 e enriquecido_rf=1 para CNPJs que ja tem dados uteis da RF.
    Criterio: tem endereco (logradouro) + pelo menos um de: socios, capital, ou porte."""
    conn = _get_connection()
    try:
        cursor = conn.execute("""
            UPDATE cnpjs_receita
            SET detalhado = 1,
                enriquecido_rf = 1,
                fonte_detalhamento = COALESCE(NULLIF(fonte_detalhamento, ''), 'dados_abertos_rf')
            WHERE detalhado = 0
            AND logradouro IS NOT NULL AND logradouro != ''
            AND (
                (socios_json IS NOT NULL AND socios_json != '[]')
                OR (capital_social IS NOT NULL AND capital_social > 0)
                OR (porte IS NOT NULL AND porte != '' AND porte != '00')
            )
        """)
        conn.commit()
        marcados = cursor.rowcount
        if marcados > 0:
            log.info(f"[RF] {marcados:,} CNPJs marcados como detalhado=1 (enriquecidos pela RF)")
        return marcados
    finally:
        conn.close()


async def _processar_complementares(manter_arquivos: bool = False,
                                      forcar_download: bool = False,
                                      pasta_rf: str = None) -> dict:
    """Processa os 3 arquivos complementares: Empresas, Simples, Socios.
    Filtra por cnpj_basico dos CNPJs ja em cnpjs_receita.
    Rastreia cada etapa via controle_atualizacao para permitir retomada."""
    stats_total = {"empresas": {}, "simples": {}, "socios": {}, "marcados_detalhado": 0}

    # Garantir indice de expressao para acelerar UPDATEs (O(log N) em vez de O(N))
    conn = _get_connection()
    conn.execute("CREATE INDEX IF NOT EXISTS idx_receita_cnpj_basico ON cnpjs_receita(SUBSTR(cnpj, 1, 8))")
    conn.commit()
    conn.close()
    log.info("[RF] Indice idx_receita_cnpj_basico garantido")

    log.info("[RF] Carregando cnpj_basico dos CNPJs existentes...")
    cnpjs_set = _carregar_cnpjs_basicos_existentes()
    if not cnpjs_set:
        log.warning("[RF] Nenhum CNPJ em cnpjs_receita - importe Estabelecimentos primeiro!")
        return stats_total
    log.info(f"[RF] {len(cnpjs_set):,} cnpj_basico unicos para filtrar")

    # Identificador da versao RF para rastreamento por etapa
    pasta_id = pasta_rf or _pasta_recente_cache or "desconhecida"

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    ) as progress:

        # 4a. Empresas (pode ser Empresas.zip ou Empresas0-9.zip)
        if not forcar_download and _obter_controle("rf_empresas_ok") == pasta_id:
            log.info("[RF] === Empresas ja processado para esta versao RF, pulando... ===")
        else:
            log.info("[RF] === Empresas (capital social, porte, natureza) ===")
            try:
                paths = await _baixar_arquivos_complementar("Empresas", progress, forcar_download)
                emp_stats = {"lidos": 0, "atualizados": 0}
                for p in paths:
                    s = _processar_empresas_zip(p, cnpjs_set)
                    emp_stats["lidos"] += s["lidos"]
                    emp_stats["atualizados"] += s["atualizados"]
                    if not manter_arquivos:
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                stats_total["empresas"] = emp_stats
                log.info(f"[RF] Empresas: {emp_stats['atualizados']:,} CNPJs atualizados")
                _salvar_controle("rf_empresas_ok", pasta_id)
            except Exception as e:
                log.error(f"[RF] Erro ao processar Empresas: {e}")

        # Marcar detalhado apos Empresas (capital/porte ja qualificam)
        marcados = _marcar_enriquecido_rf()
        stats_total["marcados_detalhado"] += marcados

        # 4b. Simples (arquivo unico)
        if not forcar_download and _obter_controle("rf_simples_ok") == pasta_id:
            log.info("[RF] === Simples ja processado para esta versao RF, pulando... ===")
        else:
            log.info("[RF] === Simples (Simples Nacional, MEI) ===")
            try:
                paths = await _baixar_arquivos_complementar("Simples", progress, forcar_download)
                simp_stats = {"lidos": 0, "atualizados": 0}
                for p in paths:
                    s = _processar_simples_zip(p, cnpjs_set)
                    simp_stats["lidos"] += s["lidos"]
                    simp_stats["atualizados"] += s["atualizados"]
                    if not manter_arquivos:
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                stats_total["simples"] = simp_stats
                log.info(f"[RF] Simples: {simp_stats['atualizados']:,} CNPJs atualizados")
                _salvar_controle("rf_simples_ok", pasta_id)
            except Exception as e:
                log.error(f"[RF] Erro ao processar Simples: {e}")

        # Marcar detalhado apos Simples
        marcados = _marcar_enriquecido_rf()
        stats_total["marcados_detalhado"] += marcados

        # 4c. Socios (pode ser Socios.zip ou Socios0-9.zip)
        # Baixa todos primeiro, depois processa junto (socios do mesmo CNPJ podem estar em arquivos diferentes)
        if not forcar_download and _obter_controle("rf_socios_ok") == pasta_id:
            log.info("[RF] === Socios ja processado para esta versao RF, pulando... ===")
        else:
            log.info("[RF] === Socios (QSA) ===")
            try:
                paths = await _baixar_arquivos_complementar("Socios", progress, forcar_download)
                stats_total["socios"] = _processar_socios_zip(paths, cnpjs_set)
                log.info(f"[RF] Socios: {stats_total['socios']['cnpjs_com_socios']:,} CNPJs com socios")
                if not manter_arquivos:
                    for p in paths:
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                _salvar_controle("rf_socios_ok", pasta_id)
            except Exception as e:
                log.error(f"[RF] Erro ao processar Socios: {e}")

        # Marcar detalhado apos Socios (socios_json qualifica)
        marcados = _marcar_enriquecido_rf()
        stats_total["marcados_detalhado"] += marcados

    return stats_total


async def processar_complementares_standalone(manter_arquivos: bool = False,
                                                forcar_download: bool = False) -> dict:
    """Wrapper publico para processar arquivos complementares da RF
    (Empresas + Simples + Socios) sem re-importar Estabelecimentos.
    Requer que cnpjs_receita ja tenha dados de Estabelecimentos."""
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM cnpjs_receita").fetchone()[0]
    enriquecidos = conn.execute(
        "SELECT COUNT(*) FROM cnpjs_receita WHERE enriquecido_rf = 1"
    ).fetchone()[0]
    conn.close()

    log.info(f"[RF] Base atual: {total:,} CNPJs | {enriquecidos:,} ja enriquecidos")

    if total == 0:
        log.warning("[RF] Nenhum CNPJ em cnpjs_receita - importe Estabelecimentos primeiro (opcao A)!")
        return {}

    # Resolver pasta RF para rastreamento por etapa
    await _resolver_url_base()
    pasta_rf = _pasta_recente_cache

    stats = await _processar_complementares(manter_arquivos, forcar_download, pasta_rf=pasta_rf)

    # Estatisticas finais
    conn = sqlite3.connect(DB_PATH)
    novo_enriquecidos = conn.execute(
        "SELECT COUNT(*) FROM cnpjs_receita WHERE enriquecido_rf = 1"
    ).fetchone()[0]
    com_socios = conn.execute(
        "SELECT COUNT(*) FROM cnpjs_receita WHERE socios_json IS NOT NULL AND socios_json != '[]'"
    ).fetchone()[0]
    conn.close()

    log.info(f"[RF] Enriquecimento concluido!")
    log.info(f"[RF]   Detalhado=1: {novo_enriquecidos:,} CNPJs")
    log.info(f"[RF]   Com socios: {com_socios:,} CNPJs")

    return stats


# ============================================================
# SELECAO INTERATIVA DE CIDADES
# ============================================================

def selecionar_modo_importacao():
    """Menu interativo para escolher modo de importacao."""
    print("""
    ┌──────────────────────────────────────────────────┐
    │       IMPORTAR DADOS ABERTOS RECEITA FEDERAL      │
    ├──────────────────────────────────────────────────┤
    │  [1] Todas as capitais (27 cidades)               │
    │  [2] Por estado (todas as cidades do estado)      │
    │  [3] Por cidades especificas                      │
    └──────────────────────────────────────────────────┘
    """)
    return input("Modo (1/2/3): ").strip()


def selecionar_estado():
    """Menu para selecionar um estado."""
    print("\n  Selecione o estado:")
    for i, uf in enumerate(UFS_BRASIL, 1):
        print(f"  [{i:2d}] {uf}", end="   " if i % 6 != 0 else "\n")
    print()
    escolha = input("Escolha: ").strip()
    try:
        idx = int(escolha) - 1
        if 0 <= idx < len(UFS_BRASIL):
            return UFS_BRASIL[idx]
    except ValueError:
        # Aceitar sigla direta
        uf = escolha.upper().strip()
        if uf in UFS_BRASIL:
            return uf
    print("[ERRO] Estado invalido.")
    return None


def selecionar_cidades_especificas():
    """Pede ao usuario as cidades no formato Cidade/UF."""
    print("\n  Digite as cidades (formato: Cidade/UF)")
    print("  Separe por virgula. Ex: Campinas/SP, Santos/SP, Guarulhos/SP")
    entrada = input("\n  Cidades: ").strip()
    if not entrada:
        return []

    cidades = []
    for item in entrada.split(","):
        item = item.strip()
        if "/" in item:
            partes = item.rsplit("/", 1)
            cidade = partes[0].strip()
            uf = partes[1].strip().upper()
            if cidade and uf and len(uf) == 2:
                cidades.append((cidade, uf))
            else:
                print(f"  [WARN] Formato invalido: {item}")
        else:
            print(f"  [WARN] Use formato Cidade/UF: {item}")

    return cidades


# ============================================================
# ORQUESTRADOR PRINCIPAL
# ============================================================

async def importar_receita_federal(modo: str, cidades_alvo: list = None,
                                    uf_alvo: str = None,
                                    manter_arquivos: bool = False,
                                    forcar_download: bool = False) -> dict:
    """
    Importa dados abertos da Receita Federal.

    Args:
        modo: 'capitais', 'estado' ou 'cidades'
        cidades_alvo: lista de (cidade, uf) para modo 'cidades'
        uf_alvo: sigla do estado para modo 'estado'
        manter_arquivos: se True, nao deleta ZIPs apos processar
        forcar_download: se True, re-baixa mesmo se arquivo existe

    Returns:
        dict com estatisticas totais
    """
    from receita_fetcher import init_tabela_receita
    init_tabela_receita()

    # 1. Carregar mapeamento de municipios e verificar atualizacao
    log.info("[RF] Carregando mapeamento de municipios...")
    municipios_map = await carregar_municipios(forcar=forcar_download)

    # Resolver URL e verificar se dados ja estao atualizados
    await _resolver_url_base()
    pasta_remota = _pasta_recente_cache  # ex: "2026-01-11"
    pasta_local = _obter_controle("rf_ultima_pasta")

    if not forcar_download and pasta_remota and pasta_local:
        if pasta_remota == pasta_local:
            log.info(f"[RF] Dados ja atualizados (pasta: {pasta_local}).")
            resp = input(f"  Dados RF ja importados ({pasta_local}). Importar novamente? [s/N]: ").strip().lower()
            if resp != "s":
                log.info("[RF] Importacao cancelada pelo usuario.")
                return {"lidos": 0, "filtrados": 0, "inseridos": 0, "ignorados": 0}
        else:
            log.info(f"[RF] Nova atualizacao disponivel: {pasta_remota} (ultima importada: {pasta_local})")
    elif pasta_remota and not pasta_local:
        log.info(f"[RF] Primeira importacao - pasta: {pasta_remota}")

    # 2. Determinar filtros baseado no modo
    codigos_alvo = set()
    ufs_alvo = set()

    if modo == 'capitais':
        for cap in CAPITAIS:
            ufs_alvo.add(cap["uf"])
            codes = _nome_para_codigos(municipios_map, cap["cidade"])
            codigos_alvo.update(codes)
            if not codes:
                log.warning(f"[RF] Municipio nao encontrado no mapeamento: {cap['cidade']}/{cap['uf']}")
        log.info(f"[RF] Modo CAPITAIS: {len(CAPITAIS)} capitais, "
                 f"{len(codigos_alvo)} codigos municipio, {len(ufs_alvo)} UFs")

    elif modo == 'estado':
        if not uf_alvo:
            log.error("[RF] Estado nao informado!")
            return {"lidos": 0, "filtrados": 0, "inseridos": 0}
        ufs_alvo.add(uf_alvo.upper())
        codigos_alvo = None  # Sem filtro de municipio (todas as cidades do estado)
        log.info(f"[RF] Modo ESTADO: {uf_alvo} (todas as cidades)")

    elif modo == 'cidades':
        if not cidades_alvo:
            log.error("[RF] Nenhuma cidade selecionada!")
            return {"lidos": 0, "filtrados": 0, "inseridos": 0}
        for cidade, uf in cidades_alvo:
            ufs_alvo.add(uf.upper())
            codes = _nome_para_codigos(municipios_map, cidade)
            codigos_alvo.update(codes)
            if not codes:
                log.warning(f"[RF] Municipio nao encontrado: {cidade}/{uf}")
        log.info(f"[RF] Modo CIDADES: {len(cidades_alvo)} cidades, "
                 f"{len(codigos_alvo)} codigos, {len(ufs_alvo)} UFs")

    # 3. Download e processamento dos 10 arquivos
    total_stats = {"lidos": 0, "filtrados": 0, "inseridos": 0, "ignorados": 0}

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    ) as progress:

        for i in range(NUM_ARQUIVOS_ESTABELECIMENTOS):
            nome = f"Estabelecimentos{i}.zip"

            # Download
            task_dl = progress.add_task(f"Baixando {nome}", total=0)
            try:
                zip_path = await baixar_arquivo(i, progress, task_dl, forcar=forcar_download)
            except Exception as e:
                log.error(f"[RF] Erro ao baixar {nome}: {e}")
                progress.remove_task(task_dl)
                continue
            progress.remove_task(task_dl)

            # Processar
            log.info(f"[RF] Processando {nome}...")
            try:
                stats = _processar_zip(
                    zip_path, codigos_alvo, ufs_alvo, municipios_map, i
                )
            except Exception as e:
                log.error(f"[RF] Erro ao processar {nome}: {e}")
                continue

            for k in total_stats:
                total_stats[k] += stats.get(k, 0)

            log.info(f"[RF] {nome}: {stats['filtrados']:,} filtrados, "
                     f"{stats['inseridos']:,} novos, "
                     f"{stats['ignorados']:,} ja existentes")

            # Deletar ZIP para economizar disco
            if not manter_arquivos:
                try:
                    os.remove(zip_path)
                    log.info(f"[RF] {nome} deletado para economizar disco")
                except OSError:
                    pass

    # 4. Processar arquivos complementares (Empresas, Simples, Socios)
    if total_stats["inseridos"] > 0 or total_stats["filtrados"] > 0:
        log.info(f"[RF] {'='*50}")
        log.info(f"[RF] PROCESSANDO ARQUIVOS COMPLEMENTARES (Empresas + Simples + Socios)")
        log.info(f"[RF] {'='*50}")
        try:
            stats_compl = await _processar_complementares(
                manter_arquivos=manter_arquivos,
                forcar_download=forcar_download,
                pasta_rf=pasta_remota,
            )
            total_stats["empresas_atualizados"] = stats_compl.get("empresas", {}).get("atualizados", 0)
            total_stats["simples_atualizados"] = stats_compl.get("simples", {}).get("atualizados", 0)
            total_stats["socios_cnpjs"] = stats_compl.get("socios", {}).get("cnpjs_com_socios", 0)
            total_stats["marcados_detalhado"] = stats_compl.get("marcados_detalhado", 0)
        except Exception as e:
            log.error(f"[RF] Erro nos complementares: {e}")

    # 5. Salvar controle de atualizacao
    if _pasta_recente_cache and total_stats["inseridos"] > 0:
        _salvar_controle("rf_ultima_pasta", _pasta_recente_cache)
        _salvar_controle("rf_ultima_importacao", datetime.now().isoformat())
        _salvar_controle("rf_modo", modo)
        log.info(f"[RF] Controle salvo: pasta={_pasta_recente_cache}, modo={modo}")

    # 6. Resumo final
    modo_label = {
        "capitais": f"CAPITAIS ({len(CAPITAIS)} cidades)",
        "estado": f"ESTADO {uf_alvo or '?'}",
        "cidades": f"CIDADES ESPECIFICAS ({len(cidades_alvo) if cidades_alvo else 0} cidades)",
    }.get(modo, modo.upper())

    log.info(f"[RF] {'=' * 50}")
    log.info(f"[RF] RESUMO IMPORTACAO RECEITA FEDERAL")
    log.info(f"[RF] MODO: {modo_label}")
    if _pasta_recente_cache:
        log.info(f"[RF] PASTA RF: {_pasta_recente_cache}")
    log.info(f"[RF] {'=' * 50}")
    log.info(f"  Linhas lidas:     {total_stats['lidos']:>12,}")
    log.info(f"  Registros alvo:   {total_stats['filtrados']:>12,}")
    log.info(f"  CNPJs novos:      {total_stats['inseridos']:>12,}")
    log.info(f"  Ja existentes:    {total_stats['ignorados']:>12,}")
    if total_stats.get("empresas_atualizados"):
        log.info(f"  Empresas upd:     {total_stats['empresas_atualizados']:>12,}")
    if total_stats.get("simples_atualizados"):
        log.info(f"  Simples upd:      {total_stats['simples_atualizados']:>12,}")
    if total_stats.get("socios_cnpjs"):
        log.info(f"  CNPJs c/ socios:  {total_stats['socios_cnpjs']:>12,}")
    if total_stats.get("marcados_detalhado"):
        log.info(f"  Detalhado=1 (RF): {total_stats['marcados_detalhado']:>12,}")

    # Estatisticas por cidade
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT cidade, uf, COUNT(*) as total
            FROM cnpjs_receita
            WHERE fonte = 'dados_abertos'
            GROUP BY cidade, uf
            ORDER BY total DESC
        """).fetchall()
        if rows:
            log.info(f"\n  {'Cidade':<25} {'UF':<4} {'CNPJs':>8}")
            log.info(f"  {'─' * 40}")
            for row in rows[:30]:
                log.info(f"  {row['cidade']:<25} {row['uf']:<4} {row['total']:>8,}")
            if len(rows) > 30:
                log.info(f"  ... +{len(rows) - 30} cidades")
    finally:
        conn.close()

    return total_stats
