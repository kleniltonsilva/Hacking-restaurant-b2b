"""
sync_crm.py - Sincroniza dados do SQLite local → PostgreSQL remoto (Derekh CRM)

Uso:
    python sync_crm.py                           # Sync tudo (incremental)
    python sync_crm.py --cidade MACEIO --uf AL   # Sync só uma cidade
    python sync_crm.py --full                     # Sync completo (ignora synced_at)

Pré-requisito:
    export DATABASE_URL=postgres://user:pass@host:5432/derekh_crm
    Ou usar fly proxy: fly proxy 15432:5432 --app <pg-app>
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

import psycopg2
from psycopg2.extras import execute_values

# Path do SQLite local
SQLITE_PATH = os.path.join(os.path.dirname(__file__), "data", "restaurants.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_sqlite_conn():
    conn = sqlite3.connect(SQLITE_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_pg_conn():
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL)
    # Fallback: conexão local via Unix socket (peer auth)
    try:
        return psycopg2.connect(dbname="derekh_crm", user=os.environ.get("USER", "klenilton"), host="/var/run/postgresql")
    except Exception:
        print("[ERRO] DATABASE_URL não definida e conexão local falhou.")
        print("       Use: export DATABASE_URL=postgres://user:pass@host:5432/derekh_crm")
        sys.exit(1)


# Colunas do SQLite cnpjs_receita que existem de fato
# Mapeamento: chave = coluna SQLite, valor = coluna PostgreSQL (leads)
CAMPOS_RECEITA_MAP = {
    "cnpj": "cnpj",
    "razao_social": "razao_social",
    "nome_fantasia": "nome_fantasia",
    "logradouro": "logradouro",
    "numero": "numero",
    "complemento": "complemento",
    "bairro": "bairro",
    "cidade": "cidade",
    "uf": "uf",
    "cep": "cep",
    "telefone1": "telefone1",
    "telefone2": "telefone2",
    "email": "email",
    "cnae_principal": "cnae_principal",
    "data_abertura": "data_abertura",
    "situacao_cadastral": "situacao_cadastral",
    "tipo_empresa": "tipo_empresa",
    "tipo_negocio": "tipo_negocio",
    "capital_social": "capital_social",
    "porte": "porte",
    "natureza_juridica": "natureza_juridica",
    "simples": "simples",
    "mei": "mei",
    "data_opcao_simples": "data_opcao_simples",
    "socios_json": "socios_json",
    "telefone_proprietario": "telefone_proprietario",
    "email_proprietario": "email_proprietario",
    "tem_ifood": "tem_ifood",
    "ifood_nome": "nome_ifood",
    "ifood_url": "url_ifood",
    "tem_rappi": "tem_rappi",
    "rappi_nome": "nome_rappi",
    "rappi_url": "url_rappi",
    "tem_99food": "tem_99food",
    "food99_nome": "nome_99food",
    "food99_url": "url_99food",
    "multi_restaurante": "multi_restaurante",
}

# Colunas do Maps (join com restaurantes)
CAMPOS_MAPS = [
    "rating", "total_reviews", "website", "google_maps_url",
    "categoria", "nome", "endereco", "telefone",
]

# Mapeamento Maps → leads
MAPS_TO_LEADS = {
    "nome": "nome_maps",
    "endereco": "endereco_maps",
    "telefone": "telefone_maps",
    "website": "website",
    "rating": "rating",
    "total_reviews": "total_reviews",
    "google_maps_url": "google_maps_url",
    "categoria": "categoria",
}


def sync(cidade: str = None, uf: str = None, full: bool = False, batch_size: int = 2000):
    """Sincroniza dados do SQLite local para PostgreSQL remoto."""
    sqlite_conn = get_sqlite_conn()
    pg_conn = get_pg_conn()

    print(f"[SYNC] Conectado ao SQLite: {SQLITE_PATH}")
    print(f"[SYNC] Conectado ao PostgreSQL: {DATABASE_URL[:50]}...")

    # Montar query SQLite
    where = []
    params = []
    if cidade:
        where.append("cr.cidade = ?")
        params.append(cidade.upper())
    if uf:
        where.append("cr.uf = ?")
        params.append(uf.upper())

    where_clause = " AND ".join(where) if where else "1=1"

    # Buscar dados do SQLite com JOIN restaurantes
    sqlite_cols = list(CAMPOS_RECEITA_MAP.keys())
    campos_select = ", ".join(f"cr.{c}" for c in sqlite_cols)
    maps_select = ", ".join(f"r.{c} as maps_{c}" for c in CAMPOS_MAPS)

    query = f"""
        SELECT {campos_select},
               cr.restaurante_id,
               {maps_select}
        FROM cnpjs_receita cr
        LEFT JOIN restaurantes r ON cr.restaurante_id = r.id
        WHERE {where_clause}
    """

    cursor_sqlite = sqlite_conn.cursor()
    cursor_sqlite.execute(query, params)

    total = 0
    erros = 0

    pg_cur = pg_conn.cursor()

    while True:
        rows = cursor_sqlite.fetchmany(batch_size)
        if not rows:
            break

        for row in rows:
            total += 1
            row_dict = dict(row)

            # Montar dados para upsert usando mapeamento SQLite → PG
            lead_data = {}
            for sqlite_col, pg_col in CAMPOS_RECEITA_MAP.items():
                lead_data[pg_col] = row_dict.get(sqlite_col)

            # Adicionar dados do Maps
            for maps_campo, leads_campo in MAPS_TO_LEADS.items():
                val = row_dict.get(f"maps_{maps_campo}")
                if val is not None:
                    # Corrigir rating com vírgula (locale BR)
                    if leads_campo in ("rating",) and isinstance(val, str):
                        val = val.replace(",", ".")
                        try:
                            val = float(val)
                        except ValueError:
                            val = None
                    lead_data[leads_campo] = val

            lead_data["synced_at"] = datetime.now()

            # Converter strings vazias para None em campos numéricos/inteiros
            CAMPOS_INT = {"total_reviews", "tem_ifood", "tem_rappi", "tem_99food", "multi_restaurante"}
            CAMPOS_FLOAT = {"rating", "capital_social"}
            for campo in CAMPOS_INT:
                val = lead_data.get(campo)
                if val is not None and isinstance(val, str):
                    val = val.strip()
                    if val == "":
                        lead_data[campo] = None
                    else:
                        try:
                            lead_data[campo] = int(float(val.replace(",", ".")))
                        except (ValueError, TypeError):
                            lead_data[campo] = None
            for campo in CAMPOS_FLOAT:
                val = lead_data.get(campo)
                if val is not None and isinstance(val, str):
                    val = val.strip()
                    if val == "":
                        lead_data[campo] = None
                    else:
                        try:
                            lead_data[campo] = float(val.replace(",", "."))
                        except (ValueError, TypeError):
                            lead_data[campo] = None

            # Tratar socios_json: se for string válida, manter
            sj = lead_data.get("socios_json")
            if sj and isinstance(sj, str):
                try:
                    json.loads(sj)
                except (json.JSONDecodeError, TypeError):
                    lead_data["socios_json"] = None

            # Upsert no PostgreSQL
            cnpj = lead_data.get("cnpj")
            if not cnpj:
                continue

            # Campos que NÃO devem ser sobrescritos no update (campos CRM)
            campos_crm_protegidos = {
                "lead_score", "segmento", "status_pipeline",
                "motivo_perda", "notas", "data_ultimo_contato",
                "data_proximo_contato", "email_invalido",
            }

            cols = [k for k in lead_data.keys() if k not in campos_crm_protegidos]
            placeholders = ", ".join(["%s"] * len(cols))
            col_names = ", ".join(cols)
            update_set = ", ".join(
                f"{c} = EXCLUDED.{c}" for c in cols if c != "cnpj"
            )

            values = [lead_data[c] for c in cols]

            sql = f"""
                INSERT INTO leads ({col_names})
                VALUES ({placeholders})
                ON CONFLICT (cnpj) DO UPDATE SET {update_set}
            """

            try:
                pg_cur.execute("SAVEPOINT sp")
                pg_cur.execute(sql, values)
                pg_cur.execute("RELEASE SAVEPOINT sp")
            except Exception as e:
                pg_cur.execute("ROLLBACK TO SAVEPOINT sp")
                erros += 1
                if erros <= 5:
                    print(f"[ERRO] CNPJ {cnpj}: {e}")
                elif erros == 6:
                    print(f"[ERRO] Suprimindo erros subsequentes...")
                continue

        pg_conn.commit()
        print(f"[SYNC] Processados: {total} | Erros: {erros}")

    sqlite_conn.close()
    pg_conn.close()

    print(f"\n[SYNC] Concluído!")
    print(f"  Total processados: {total}")
    print(f"  Erros: {erros}")
    print(f"  Inseridos/atualizados: {total - erros}")
    print(f"  Filtro: {'cidade=' + cidade if cidade else ''} {'uf=' + uf if uf else ''} {'(todos)' if not cidade and not uf else ''}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync SQLite local → PostgreSQL CRM")
    parser.add_argument("--cidade", type=str, help="Filtrar por cidade (ex: MACEIO)")
    parser.add_argument("--uf", type=str, help="Filtrar por UF (ex: AL)")
    parser.add_argument("--full", action="store_true", help="Sync completo (ignora incremental)")
    parser.add_argument("--batch", type=int, default=2000, help="Tamanho do batch (default: 2000)")

    args = parser.parse_args()
    sync(cidade=args.cidade, uf=args.uf, full=args.full, batch_size=args.batch)
