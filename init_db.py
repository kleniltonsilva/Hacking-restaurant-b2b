"""
init_db.py - Inicialização do banco de dados SQLite
v2.0 - Inclui tabela cnpjs_receita para base da Receita Federal
"""
import sqlite3
from config import DB_PATH


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
            capital_social REAL,
            porte TEXT,
            natureza_juridica TEXT,
            data_abertura TEXT,
            simples INTEGER,
            mei INTEGER,
            socios_json TEXT,
            fonte TEXT DEFAULT 'casadosdados',
            detalhado INTEGER DEFAULT 0,
            data_coleta TEXT,
            data_detalhamento TEXT,
            restaurante_id INTEGER,
            score_match REAL,
            matched INTEGER DEFAULT 0
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

    conn.commit()
    conn.close()
    print("[DB] ✅ Banco de dados inicializado com sucesso.")


if __name__ == "__main__":
    init_database()
