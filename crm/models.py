"""
models.py - Constantes do CRM (status pipeline, segmentos, tipos de interação, email, campanhas)
"""

# ============================================================
# PIPELINE
# ============================================================

PIPELINE_STATUS = [
    "novo",
    "contactado",
    "respondeu",
    "demo_agendada",
    "proposta_enviada",
    "negociando",
    "cliente",
    "perdido",
]

PIPELINE_LABELS = {
    "novo": "Novo",
    "contactado": "Contactado",
    "respondeu": "Respondeu",
    "demo_agendada": "Demo Agendada",
    "proposta_enviada": "Proposta Enviada",
    "negociando": "Negociando",
    "cliente": "Cliente",
    "perdido": "Perdido",
}

PIPELINE_CORES = {
    "novo": "slate",
    "contactado": "blue",
    "respondeu": "cyan",
    "demo_agendada": "violet",
    "proposta_enviada": "amber",
    "negociando": "orange",
    "cliente": "emerald",
    "perdido": "red",
}

# ============================================================
# SEGMENTOS
# ============================================================

SEGMENTOS = ["novo", "frio", "quente", "premium", "rede"]

SEGMENTO_LABELS = {
    "novo": "Novo",
    "frio": "Frio",
    "quente": "Quente",
    "premium": "Premium",
    "rede": "Rede",
}

SEGMENTO_CORES = {
    "novo": "gray",
    "frio": "blue",
    "quente": "orange",
    "premium": "green",
    "rede": "purple",
}

# ============================================================
# INTERAÇÕES
# ============================================================

TIPOS_INTERACAO = ["nota", "ligacao", "whatsapp", "email", "reuniao"]

TIPOS_INTERACAO_LABELS = {
    "nota": "Nota",
    "ligacao": "Ligação",
    "whatsapp": "WhatsApp",
    "email": "Email",
    "reuniao": "Reunião",
}

CANAIS = ["telefone", "whatsapp", "email", "presencial"]

CANAIS_LABELS = {
    "telefone": "Telefone",
    "whatsapp": "WhatsApp",
    "email": "Email",
    "presencial": "Presencial",
}

RESULTADOS_INTERACAO = ["positivo", "negativo", "sem_resposta", "agendou"]

RESULTADOS_LABELS = {
    "positivo": "Positivo",
    "negativo": "Negativo",
    "sem_resposta": "Sem Resposta",
    "agendou": "Agendou",
}

# ============================================================
# EMAIL / CAMPANHAS
# ============================================================

STATUS_CAMPANHA = ["rascunho", "enviando", "concluida", "pausada"]

STATUS_CAMPANHA_LABELS = {
    "rascunho": "Rascunho",
    "enviando": "Enviando",
    "concluida": "Concluída",
    "pausada": "Pausada",
}

STATUS_SEQUENCIA_LEAD = ["ativo", "pausado", "concluido", "cancelado"]

CONDICOES_ETAPA = ["sempre", "nao_abriu", "abriu", "clicou", "nao_clicou"]

CONDICOES_ETAPA_LABELS = {
    "sempre": "Sempre enviar",
    "nao_abriu": "Não abriu o anterior",
    "abriu": "Abriu o anterior",
    "clicou": "Clicou no anterior",
    "nao_clicou": "Não clicou no anterior",
}

# ============================================================
# WHATSAPP TEMPLATES
# ============================================================

WHATSAPP_TEMPLATES = {
    "primeiro_contato": {
        "nome": "Primeiro Contato",
        "mensagem": (
            "Olá {nome_dono}! Tudo bem? 👋\n\n"
            "Me chamo [SEU NOME] da Derekh Food. "
            "Vi que o *{nome_restaurante}* tem {rating} estrelas e {total_avaliacoes} avaliações — parabéns pelo trabalho!\n\n"
            "A Derekh ajuda restaurantes como o seu a vender mais através de delivery próprio, "
            "sem pagar comissões de 27% para iFood.\n\n"
            "Posso te mostrar como funciona em 5 minutos?"
        ),
    },
    "sem_delivery": {
        "nome": "Sem Delivery",
        "mensagem": (
            "Olá {nome_dono}! 👋\n\n"
            "Percebi que o *{nome_restaurante}* ainda não está em plataformas de delivery. "
            "Sabia que restaurantes com delivery próprio faturam em média 30% a mais?\n\n"
            "A Derekh Food cria seu delivery em 48h, sem comissões abusivas.\n\n"
            "Posso te contar mais?"
        ),
    },
    "com_ifood": {
        "nome": "Já tem iFood",
        "mensagem": (
            "Olá {nome_dono}! 👋\n\n"
            "Vi que o *{nome_restaurante}* já está no iFood. "
            "A Derekh funciona como complemento — seu delivery próprio, sua marca, suas regras.\n\n"
            "Sem comissão de 27%. O pedido vai direto pro seu caixa.\n\n"
            "Quer saber como funciona?"
        ),
    },
    "followup": {
        "nome": "Follow-up",
        "mensagem": (
            "Oi {nome_dono}, tudo bem?\n\n"
            "Entrei em contato há alguns dias sobre o delivery próprio para o *{nome_restaurante}*.\n\n"
            "Tem 5 minutinhos para conversarmos? Posso ligar agora ou agendar o melhor horário pra você."
        ),
    },
}
