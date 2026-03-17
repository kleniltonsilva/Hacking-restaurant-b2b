-- ============================================================
-- Derekh CRM - Schema PostgreSQL
-- Database: derekh_crm (fly.io PostgreSQL)
-- ============================================================

-- Extensão para UUID (opcional, caso precise no futuro)
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- LEADS (espelho de cnpjs_receita + campos CRM + Maps)
-- ============================================================
CREATE TABLE IF NOT EXISTS leads (
    id SERIAL PRIMARY KEY,
    cnpj VARCHAR(14) NOT NULL UNIQUE,

    -- Dados Receita Federal (Estabelecimentos)
    razao_social TEXT,
    nome_fantasia TEXT,
    tipo_logradouro TEXT,
    logradouro TEXT,
    numero TEXT,
    complemento TEXT,
    bairro TEXT,
    cidade TEXT,
    uf VARCHAR(2),
    cep VARCHAR(8),
    telefone1 TEXT,
    telefone2 TEXT,
    email TEXT,
    cnae_principal VARCHAR(7),
    data_abertura TEXT,
    situacao_cadastral VARCHAR(10),

    -- Dados RF complementares (Empresas + Simples + Sócios)
    tipo_empresa TEXT,
    tipo_negocio TEXT,
    capital_social REAL,
    porte TEXT,
    natureza_juridica TEXT,
    simples VARCHAR(1),
    mei VARCHAR(1),
    data_opcao_simples TEXT,
    socios_json JSONB,

    -- Contato extra (cnpj.biz - opcional)
    telefone_proprietario TEXT,
    email_proprietario TEXT,

    -- Delivery
    tem_ifood SMALLINT DEFAULT 0,
    nome_ifood TEXT,
    url_ifood TEXT,
    tem_rappi SMALLINT DEFAULT 0,
    nome_rappi TEXT,
    url_rappi TEXT,
    tem_99food SMALLINT DEFAULT 0,
    nome_99food TEXT,
    url_99food TEXT,

    -- Dados Google Maps (desnormalizados no sync)
    rating REAL,
    total_reviews INTEGER,
    website TEXT,
    google_maps_url TEXT,
    categoria TEXT,
    nome_maps TEXT,
    endereco_maps TEXT,
    telefone_maps TEXT,

    -- Controle de rede
    multi_restaurante SMALLINT DEFAULT 0,

    -- CRM
    lead_score INTEGER DEFAULT 0,
    segmento TEXT DEFAULT 'novo',
    status_pipeline TEXT DEFAULT 'novo',
    motivo_perda TEXT,
    notas TEXT,
    data_ultimo_contato TIMESTAMP,
    data_proximo_contato DATE,
    email_invalido SMALLINT DEFAULT 0,

    -- Controle
    synced_at TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- INTERAÇÕES
-- ============================================================
CREATE TABLE IF NOT EXISTS interacoes (
    id SERIAL PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL,
    canal TEXT,
    conteudo TEXT,
    resultado TEXT,
    email_message_id TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- EMAIL TEMPLATES
-- ============================================================
CREATE TABLE IF NOT EXISTS email_templates (
    id SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    assunto TEXT NOT NULL,
    corpo_html TEXT NOT NULL,
    segmento_alvo TEXT,
    ativo BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- CAMPANHAS DE EMAIL
-- ============================================================
CREATE TABLE IF NOT EXISTS campanhas_email (
    id SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    template_id INTEGER REFERENCES email_templates(id) ON DELETE SET NULL,
    filtros_json JSONB,
    total_enviados INTEGER DEFAULT 0,
    total_abertos INTEGER DEFAULT 0,
    total_clicados INTEGER DEFAULT 0,
    total_bounced INTEGER DEFAULT 0,
    status TEXT DEFAULT 'rascunho',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- SEQUÊNCIAS DE EMAIL
-- ============================================================
CREATE TABLE IF NOT EXISTS sequencias_email (
    id SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    descricao TEXT,
    ativo BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sequencia_etapas (
    id SERIAL PRIMARY KEY,
    sequencia_id INTEGER NOT NULL REFERENCES sequencias_email(id) ON DELETE CASCADE,
    ordem INTEGER NOT NULL,
    template_id INTEGER NOT NULL REFERENCES email_templates(id) ON DELETE CASCADE,
    dias_espera INTEGER NOT NULL DEFAULT 1,
    condicao TEXT DEFAULT 'sempre',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lead_sequencia (
    id SERIAL PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    sequencia_id INTEGER NOT NULL REFERENCES sequencias_email(id) ON DELETE CASCADE,
    etapa_atual INTEGER DEFAULT 1,
    status TEXT DEFAULT 'ativo',
    proximo_envio TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(lead_id, sequencia_id)
);

-- ============================================================
-- ÍNDICES
-- ============================================================

-- Leads
CREATE INDEX IF NOT EXISTS idx_leads_cidade_uf ON leads(cidade, uf);
CREATE INDEX IF NOT EXISTS idx_leads_lead_score ON leads(lead_score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_segmento ON leads(segmento);
CREATE INDEX IF NOT EXISTS idx_leads_status_pipeline ON leads(status_pipeline);
CREATE INDEX IF NOT EXISTS idx_leads_proximo_contato ON leads(data_proximo_contato);
CREATE INDEX IF NOT EXISTS idx_leads_synced_at ON leads(synced_at);
CREATE INDEX IF NOT EXISTS idx_leads_uf ON leads(uf);

-- Interações
CREATE INDEX IF NOT EXISTS idx_interacoes_lead_id ON interacoes(lead_id);
CREATE INDEX IF NOT EXISTS idx_interacoes_created ON interacoes(created_at DESC);

-- Campanhas
CREATE INDEX IF NOT EXISTS idx_campanhas_status ON campanhas_email(status);

-- Sequências
CREATE INDEX IF NOT EXISTS idx_lead_sequencia_proximo ON lead_sequencia(proximo_envio)
    WHERE status = 'ativo';
CREATE INDEX IF NOT EXISTS idx_sequencia_etapas_seq ON sequencia_etapas(sequencia_id, ordem);

-- ============================================================
-- TRIGGER: updated_at automático
-- ============================================================
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    CREATE TRIGGER set_updated_at_leads
        BEFORE UPDATE ON leads
        FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TRIGGER set_updated_at_campanhas
        BEFORE UPDATE ON campanhas_email
        FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TRIGGER set_updated_at_templates
        BEFORE UPDATE ON email_templates
        FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
