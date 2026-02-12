"""
db_manager.py - Gerenciador do banco de dados SQLite
Responsável por todas as operações CRUD e consultas.
"""
import sqlite3
from datetime import datetime
from typing import Optional
from config import DB_PATH, STATUS_PENDENTE


def get_connection():
    """Retorna uma conexão com o banco configurada."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Melhor performance para escritas
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ============================================================
# RESTAURANTES - CRUD
# ============================================================

def inserir_restaurante(dados: dict) -> Optional[int]:
    """
    Insere um restaurante no banco. Ignora duplicatas (nome+cidade+uf).
    Retorna o ID do registro ou None se já existia.
    """
    conn = get_connection()
    try:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO restaurantes 
            (nome, endereco, telefone, website, cidade, uf, latitude, longitude,
             google_maps_url, rating, total_reviews, categoria, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            dados.get("nome", ""),
            dados.get("endereco", ""),
            dados.get("telefone", ""),
            dados.get("website", ""),
            dados.get("cidade", ""),
            dados.get("uf", ""),
            dados.get("latitude", ""),
            dados.get("longitude", ""),
            dados.get("google_maps_url", ""),
            dados.get("rating", ""),
            dados.get("total_reviews", ""),
            dados.get("categoria", ""),
            STATUS_PENDENTE,
        ))
        conn.commit()
        if cursor.rowcount > 0:
            return cursor.lastrowid
        return None
    except Exception as e:
        print(f"[DB ERRO] Inserção falhou: {e}")
        return None
    finally:
        conn.close()


def inserir_restaurantes_batch(lista_dados: list) -> int:
    """Insere múltiplos restaurantes de uma vez. Retorna qtd inseridos."""
    conn = get_connection()
    inseridos = 0
    try:
        for dados in lista_dados:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO restaurantes 
                (nome, endereco, telefone, website, cidade, uf, latitude, longitude,
                 google_maps_url, rating, total_reviews, categoria, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                dados.get("nome", ""),
                dados.get("endereco", ""),
                dados.get("telefone", ""),
                dados.get("website", ""),
                dados.get("cidade", ""),
                dados.get("uf", ""),
                dados.get("latitude", ""),
                dados.get("longitude", ""),
                dados.get("google_maps_url", ""),
                dados.get("rating", ""),
                dados.get("total_reviews", ""),
                dados.get("categoria", ""),
                STATUS_PENDENTE,
            ))
            if cursor.rowcount > 0:
                inseridos += 1
        conn.commit()
    except Exception as e:
        print(f"[DB ERRO] Batch insert falhou: {e}")
        conn.rollback()
    finally:
        conn.close()
    return inseridos


def atualizar_status(restaurante_id: int, novo_status: str):
    """Atualiza o status de um restaurante."""
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE restaurantes 
            SET status = ?, data_atualizacao = ?
            WHERE id = ?
        """, (novo_status, datetime.now().isoformat(), restaurante_id))
        conn.commit()
    finally:
        conn.close()


def atualizar_ifood(restaurante_id: int, tem_ifood: bool, ifood_nome: str = "", ifood_url: str = ""):
    """Atualiza informações do iFood para um restaurante."""
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE restaurantes 
            SET tem_ifood = ?, ifood_nome = ?, ifood_url = ?, 
                status = 'ifood_checked', data_atualizacao = ?
            WHERE id = ?
        """, (1 if tem_ifood else 0, ifood_nome, ifood_url, 
              datetime.now().isoformat(), restaurante_id))
        conn.commit()
    finally:
        conn.close()


def atualizar_cnpj(restaurante_id: int, dados_cnpj: dict):
    """Atualiza dados do CNPJ para um restaurante (inclui email e telefone da Receita)."""
    conn = get_connection()
    try:
        # Telefones como string separada por pipe
        telefones = dados_cnpj.get("telefones_receita", [])
        telefones_str = " | ".join(telefones) if isinstance(telefones, list) else str(telefones)

        conn.execute("""
            UPDATE restaurantes 
            SET cnpj = ?, razao_social = ?, nome_fantasia = ?,
                situacao_cadastral = ?, data_abertura = ?,
                natureza_juridica = ?, capital_social = ?,
                email_receita = ?, telefones_receita = ?,
                porte_empresa = ?, simples = ?, mei = ?,
                score_confianca = ?,
                status = 'enriquecido', data_atualizacao = ?
            WHERE id = ?
        """, (
            dados_cnpj.get("cnpj", ""),
            dados_cnpj.get("razao_social", ""),
            dados_cnpj.get("nome_fantasia", ""),
            dados_cnpj.get("situacao_cadastral", ""),
            dados_cnpj.get("data_abertura", ""),
            dados_cnpj.get("natureza_juridica", ""),
            dados_cnpj.get("capital_social", 0),
            dados_cnpj.get("email_receita", ""),
            telefones_str,
            dados_cnpj.get("porte_empresa", ""),
            1 if dados_cnpj.get("simples") else 0,
            1 if dados_cnpj.get("mei") else 0,
            dados_cnpj.get("score_confianca", 0),
            datetime.now().isoformat(),
            restaurante_id,
        ))
        conn.commit()
    finally:
        conn.close()


def inserir_socios(restaurante_id: int, socios: list):
    """Insere a lista de sócios de um restaurante."""
    conn = get_connection()
    try:
        # Limpar sócios antigos
        conn.execute("DELETE FROM socios WHERE restaurante_id = ?", (restaurante_id,))
        for socio in socios:
            conn.execute("""
                INSERT INTO socios 
                (restaurante_id, nome_socio, qualificacao, tipo, cpf_cnpj_socio,
                 data_entrada, faixa_etaria)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                restaurante_id,
                socio.get("nome", ""),
                socio.get("qualificacao", ""),
                socio.get("tipo", ""),
                socio.get("cpf_cnpj", ""),
                socio.get("data_entrada", ""),
                socio.get("faixa_etaria", ""),
            ))
        conn.commit()
    finally:
        conn.close()


# ============================================================
# CONSULTAS
# ============================================================

def buscar_por_cidade(cidade: str, uf: str) -> list:
    """Retorna todos os restaurantes de uma cidade."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM restaurantes WHERE cidade = ? AND uf = ?
            ORDER BY nome
        """, (cidade, uf)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def buscar_pendentes(status: str, cidade: str = None, uf: str = None, limite: int = 100) -> list:
    """Retorna restaurantes com determinado status para processamento."""
    conn = get_connection()
    try:
        query = "SELECT * FROM restaurantes WHERE status = ?"
        params = [status]
        if cidade and uf:
            query += " AND cidade = ? AND uf = ?"
            params.extend([cidade, uf])
        query += f" LIMIT {limite}"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def buscar_socios_restaurante(restaurante_id: int) -> list:
    """Retorna os sócios de um restaurante."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM socios WHERE restaurante_id = ?
        """, (restaurante_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def estatisticas_gerais() -> dict:
    """Retorna estatísticas gerais do banco."""
    conn = get_connection()
    try:
        stats = {}
        stats["total"] = conn.execute("SELECT COUNT(*) FROM restaurantes").fetchone()[0]
        stats["pendentes"] = conn.execute(
            "SELECT COUNT(*) FROM restaurantes WHERE status = 'pendente'"
        ).fetchone()[0]
        stats["processados"] = conn.execute(
            "SELECT COUNT(*) FROM restaurantes WHERE status = 'processado'"
        ).fetchone()[0]
        stats["ifood_checked"] = conn.execute(
            "SELECT COUNT(*) FROM restaurantes WHERE status = 'ifood_checked'"
        ).fetchone()[0]
        stats["enriquecidos"] = conn.execute(
            "SELECT COUNT(*) FROM restaurantes WHERE status = 'enriquecido'"
        ).fetchone()[0]
        stats["com_ifood"] = conn.execute(
            "SELECT COUNT(*) FROM restaurantes WHERE tem_ifood = 1"
        ).fetchone()[0]
        stats["com_cnpj"] = conn.execute(
            "SELECT COUNT(*) FROM restaurantes WHERE cnpj IS NOT NULL AND cnpj != ''"
        ).fetchone()[0]
        stats["total_socios"] = conn.execute("SELECT COUNT(*) FROM socios").fetchone()[0]

        # Por cidade
        rows = conn.execute("""
            SELECT cidade, uf, COUNT(*) as total,
                   SUM(CASE WHEN tem_ifood = 1 THEN 1 ELSE 0 END) as com_ifood,
                   SUM(CASE WHEN cnpj IS NOT NULL AND cnpj != '' THEN 1 ELSE 0 END) as com_cnpj
            FROM restaurantes
            GROUP BY cidade, uf
            ORDER BY total DESC
        """).fetchall()
        stats["por_cidade"] = [dict(r) for r in rows]

        return stats
    finally:
        conn.close()


def buscar_todos_para_export(cidade: str = None, uf: str = None) -> list:
    """Retorna dados completos para exportação, incluindo sócios."""
    conn = get_connection()
    try:
        query = """
            SELECT r.*, 
                   GROUP_CONCAT(s.nome_socio, ' | ') as socios_nomes,
                   GROUP_CONCAT(s.qualificacao, ' | ') as socios_qualificacoes
            FROM restaurantes r
            LEFT JOIN socios s ON r.id = s.restaurante_id
        """
        params = []
        if cidade and uf:
            query += " WHERE r.cidade = ? AND r.uf = ?"
            params = [cidade, uf]
        query += " GROUP BY r.id ORDER BY r.cidade, r.nome"
        
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def resetar_banco():
    """Remove TODOS os dados do banco. USE COM CUIDADO."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM socios")
        conn.execute("DELETE FROM restaurantes")
        conn.execute("DELETE FROM varreduras")
        conn.execute("DELETE FROM sqlite_sequence")
        conn.commit()
        print("[DB] 🗑️  Banco resetado com sucesso.")
    finally:
        conn.close()


# ============================================================
# VARREDURAS - CONTROLE
# ============================================================

def registrar_varredura(cidade: str, uf: str):
    """Registra ou atualiza uma varredura para uma cidade."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO varreduras (cidade, uf, status, inicio)
            VALUES (?, ?, 'em_andamento', ?)
        """, (cidade, uf, datetime.now().isoformat()))
        conn.commit()
    finally:
        conn.close()


def finalizar_varredura(cidade: str, uf: str, total: int):
    """Marca uma varredura como concluída."""
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE varreduras 
            SET status = 'concluida', total_encontrados = ?, fim = ?
            WHERE cidade = ? AND uf = ?
        """, (total, datetime.now().isoformat(), cidade, uf))
        conn.commit()
    finally:
        conn.close()


def cidades_ja_varridas() -> list:
    """Retorna lista de cidades que já foram varridas."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT cidade, uf, total_encontrados, status 
            FROM varreduras ORDER BY cidade
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
