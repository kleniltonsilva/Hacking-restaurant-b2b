"""
init_db.py - Inicialização do banco de dados SQLite
v4.0 - RF Expandido (Empresas/Simples/Socios) + Multi-delivery (Rappi/99Food)
"""
import sqlite3
from config import DB_PATH
from logger import log


def _migrar_banco(conn):
    """Adiciona colunas novas via ALTER TABLE (seguro para bancos existentes)."""
    cursor = conn.cursor()

    # Colunas novas em cnpjs_receita
    migracoes_receita = [
        ("telefone_proprietario", "TEXT"),
        ("tem_ifood", "INTEGER DEFAULT 0"),
        ("ifood_nome", "TEXT"),
        ("ifood_url", "TEXT"),
        ("tipo_empresa", "TEXT"),
        ("fonte_detalhamento", "TEXT"),
        ("multi_restaurante", "INTEGER DEFAULT 0"),
        ("tentativas_falha", "INTEGER DEFAULT 0"),
        ("ultima_falha", "TEXT"),
        # v4.0 - RF Expandido
        ("enriquecido_rf", "INTEGER DEFAULT 0"),
        ("email_proprietario", "TEXT"),
        ("tipo_negocio", "TEXT"),
        ("data_opcao_simples", "TEXT"),
        ("data_situacao_cadastral", "TEXT"),
        ("cnpjbiz_inexistente", "INTEGER DEFAULT 0"),
        # v4.0 - Multi-delivery
        ("tem_rappi", "INTEGER DEFAULT 0"),
        ("rappi_nome", "TEXT"),
        ("rappi_url", "TEXT"),
        ("tem_99food", "INTEGER DEFAULT 0"),
        ("food99_nome", "TEXT"),
        ("food99_url", "TEXT"),
    ]
    for coluna, tipo in migracoes_receita:
        try:
            cursor.execute(f"ALTER TABLE cnpjs_receita ADD COLUMN {coluna} {tipo}")
        except sqlite3.OperationalError:
            pass  # Coluna já existe

    # Colunas novas em restaurantes
    migracoes_restaurantes = [
        ("telefone_proprietario", "TEXT"),
        # v4.0 - Multi-delivery
        ("tem_rappi", "INTEGER DEFAULT 0"),
        ("rappi_nome", "TEXT"),
        ("rappi_url", "TEXT"),
        ("tem_99food", "INTEGER DEFAULT 0"),
        ("food99_nome", "TEXT"),
        ("food99_url", "TEXT"),
    ]
    for coluna, tipo in migracoes_restaurantes:
        try:
            cursor.execute(f"ALTER TABLE restaurantes ADD COLUMN {coluna} {tipo}")
        except sqlite3.OperationalError:
            pass  # Coluna já existe

    conn.commit()


def init_database():
    """Cria as tabelas do banco de dados se não existirem."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Tabela principal de restaurantes (Google Maps)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS restaurantes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            endereco TEXT,
            telefone TEXT,
            website TEXT,
            cidade TEXT NOT NULL,
            uf TEXT NOT NULL,
            latitude TEXT,
            longitude TEXT,
            google_maps_url TEXT,
            rating TEXT,
            total_reviews TEXT,
            categoria TEXT,

            -- iFood
            tem_ifood INTEGER DEFAULT 0,
            ifood_nome TEXT,
            ifood_url TEXT,

            -- CNPJ / Societário (preenchido via cruzamento com Receita)
            cnpj TEXT,
            razao_social TEXT,
            nome_fantasia TEXT,
            situacao_cadastral TEXT,
            data_abertura TEXT,
            natureza_juridica TEXT,
            capital_social REAL,
            email_receita TEXT,
            telefones_receita TEXT,
            telefone_proprietario TEXT,
            porte_empresa TEXT,
            simples INTEGER,
            mei INTEGER,
            score_confianca REAL,

            -- Controle
            status TEXT DEFAULT 'pendente',
            data_coleta TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            data_atualizacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(nome, cidade, uf)
        )
    """)

    # Tabela de sócios (QSA)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS socios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurante_id INTEGER NOT NULL,
            nome_socio TEXT NOT NULL,
            qualificacao TEXT,
            tipo TEXT,
            cpf_cnpj_socio TEXT,
            data_entrada TEXT,
            faixa_etaria TEXT,
            FOREIGN KEY (restaurante_id) REFERENCES restaurantes(id) ON DELETE CASCADE
        )
    """)

    # Tabela de CNPJs da Receita Federal
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cnpjs_receita (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cnpj TEXT UNIQUE NOT NULL,
            razao_social TEXT,
            nome_fantasia TEXT,
            situacao_cadastral TEXT,
            cnae_principal TEXT,
            logradouro TEXT,
            numero TEXT,
            complemento TEXT,
            bairro TEXT,
            cep TEXT,
            cidade TEXT,
            uf TEXT,
            endereco_completo TEXT,
            email TEXT,
            telefone1 TEXT,
            telefone2 TEXT,
            telefone_proprietario TEXT,
            capital_social REAL,
            porte TEXT,
            natureza_juridica TEXT,
            data_abertura TEXT,
            simples INTEGER,
            mei INTEGER,
            tipo_empresa TEXT,
            socios_json TEXT,
            fonte TEXT DEFAULT 'casadosdados',
            fonte_detalhamento TEXT,
            detalhado INTEGER DEFAULT 0,
            data_coleta TEXT,
            data_detalhamento TEXT,
            tem_ifood INTEGER DEFAULT 0,
            ifood_nome TEXT,
            ifood_url TEXT,
            restaurante_id INTEGER,
            score_match REAL,
            matched INTEGER DEFAULT 0,
            multi_restaurante INTEGER DEFAULT 0,

            -- v4.0: RF Expandido
            enriquecido_rf INTEGER DEFAULT 0,

            -- v4.0: Multi-delivery
            tem_rappi INTEGER DEFAULT 0,
            rappi_nome TEXT,
            rappi_url TEXT,
            tem_99food INTEGER DEFAULT 0,
            food99_nome TEXT,
            food99_url TEXT
        )
    """)

    # Controle de varreduras da Receita
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS varreduras_receita (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cidade TEXT NOT NULL,
            uf TEXT NOT NULL,
            cnae TEXT NOT NULL,
            total_encontrados INTEGER DEFAULT 0,
            novos_inseridos INTEGER DEFAULT 0,
            data_varredura TEXT,
            UNIQUE(cidade, uf, cnae)
        )
    """)

    # Controle de varreduras do Google Maps
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS varreduras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cidade TEXT NOT NULL,
            uf TEXT NOT NULL,
            total_encontrados INTEGER DEFAULT 0,
            total_processados INTEGER DEFAULT 0,
            total_enriquecidos INTEGER DEFAULT 0,
            status TEXT DEFAULT 'em_andamento',
            inicio TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            fim TIMESTAMP,
            UNIQUE(cidade, uf)
        )
    """)

    # Tabela de controle de atualizações
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS controle_atualizacao (
            chave TEXT PRIMARY KEY,
            valor TEXT,
            data_registro TEXT
        )
    """)

    # Índices
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_restaurantes_cidade ON restaurantes(cidade, uf)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_restaurantes_status ON restaurantes(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_restaurantes_cnpj ON restaurantes(cnpj)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_restaurantes_endereco ON restaurantes(endereco)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_socios_restaurante ON socios(restaurante_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_receita_cnpj ON cnpjs_receita(cnpj)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_receita_cidade ON cnpjs_receita(cidade, uf)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_receita_endereco ON cnpjs_receita(logradouro, numero, cidade)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_receita_matched ON cnpjs_receita(matched)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_receita_detalhado ON cnpjs_receita(detalhado)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_receita_cnpj_basico ON cnpjs_receita(SUBSTR(cnpj, 1, 8))")

    # Migrar banco existente (adicionar colunas novas)
    _migrar_banco(conn)

    conn.commit()
    conn.close()
    log.info("[DB] ✅ Banco de dados inicializado com sucesso.")


if __name__ == "__main__":
    init_database()
