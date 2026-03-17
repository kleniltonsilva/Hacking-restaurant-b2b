"""
scoring.py - Lead scoring, segmentação automática e personalização de abordagem
Score de 0-100 baseado em múltiplos fatores do lead.
"""
import json
import os
from datetime import datetime, date

import psycopg2
from psycopg2.extras import RealDictCursor


DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


# ============================================================
# CÁLCULO DE SCORE
# ============================================================

def calcular_score(lead: dict) -> int:
    """Calcula lead score (0-100) com base nos atributos do lead."""
    score = 0

    # --- Delivery (oportunidade) ---
    tem_ifood = lead.get("tem_ifood") or 0
    tem_rappi = lead.get("tem_rappi") or 0
    tem_99food = lead.get("tem_99food") or 0
    total_delivery = tem_ifood + tem_rappi + tem_99food

    if total_delivery == 0:
        score += 20  # Sem nenhuma plataforma = OPORTUNIDADE MÁXIMA
    elif total_delivery > 0:
        score += 15  # Já entende delivery, lead educado

    # --- Rating Google Maps ---
    rating = lead.get("rating") or 0
    if rating >= 4.5:
        score += 25  # 15 (>= 4.0) + 10 (>= 4.5)
    elif rating >= 4.0:
        score += 15

    # --- Reviews ---
    total_reviews = lead.get("total_reviews") or 0
    if total_reviews >= 100:
        score += 15

    # --- Capital Social ---
    capital = lead.get("capital_social") or 0
    if capital >= 100000:
        score += 10

    # --- Porte ---
    porte = lead.get("porte") or ""
    if porte and porte.upper() not in ("MEI", "MICRO EMPREENDEDOR INDIVIDUAL", ""):
        score += 5  # Mais estruturado que MEI

    # --- Negócio novo ---
    data_abertura = lead.get("data_abertura")
    if data_abertura:
        try:
            if isinstance(data_abertura, str):
                # Formato pode ser YYYY-MM-DD ou YYYYMMDD
                data_abertura = data_abertura.replace("-", "")
                if len(data_abertura) == 8:
                    dt = datetime.strptime(data_abertura, "%Y%m%d")
                    meses = (datetime.now() - dt).days / 30
                    if meses <= 12:
                        score += 10  # Negócio novo, precisa de tudo
        except (ValueError, TypeError):
            pass

    # --- Canais de contato ---
    if lead.get("email") and lead["email"].strip():
        score += 5
    if lead.get("telefone1") and lead["telefone1"].strip():
        score += 5

    # --- Penalidades ---
    status = lead.get("status_pipeline") or "novo"
    if status == "perdido":
        score -= 20
    if lead.get("email_invalido"):
        score -= 10

    return max(0, min(100, score))


# ============================================================
# SEGMENTAÇÃO AUTOMÁTICA
# ============================================================

def determinar_segmento(lead: dict, score: int) -> str:
    """Determina segmento do lead baseado em score + atributos."""

    # Rede: múltiplos restaurantes
    if lead.get("multi_restaurante"):
        return "rede"

    # Premium: capital alto + não MEI + múltiplos sócios
    capital = lead.get("capital_social") or 0
    porte = lead.get("porte") or ""
    socios_json = lead.get("socios_json")
    num_socios = 0
    if socios_json:
        try:
            if isinstance(socios_json, str):
                socios = json.loads(socios_json)
            else:
                socios = socios_json
            num_socios = len(socios) if isinstance(socios, list) else 0
        except (json.JSONDecodeError, TypeError):
            pass

    if capital >= 200000 and porte.upper() not in ("MEI",) and num_socios >= 2:
        return "premium"

    # Novo: aberto há menos de 6 meses
    data_abertura = lead.get("data_abertura")
    if data_abertura:
        try:
            if isinstance(data_abertura, str):
                da = data_abertura.replace("-", "")
                if len(da) == 8:
                    dt = datetime.strptime(da, "%Y%m%d")
                    meses = (datetime.now() - dt).days / 30
                    if meses <= 6:
                        return "novo"
        except (ValueError, TypeError):
            pass

    # Quente: score alto + sem delivery + com contato
    tem_delivery = (lead.get("tem_ifood") or 0) + (lead.get("tem_rappi") or 0) + (lead.get("tem_99food") or 0)
    tem_contato = bool(lead.get("email") or lead.get("telefone1"))
    if score >= 70 and tem_delivery == 0 and tem_contato:
        return "quente"

    # Frio: score baixo ou sem contato
    if score < 30 or not tem_contato:
        return "frio"

    # Default
    return "novo"


# ============================================================
# PERSONALIZAÇÃO DE ABORDAGEM
# ============================================================

def personalizar_abordagem(lead: dict) -> dict:
    """Gera contexto de personalização para abordagem do lead."""
    resultado = {
        "nome_dono": "",
        "contexto": "",
        "abordagem": "",
        "tom": "padrao",
    }

    # Extrair nome do proprietário
    socios_json = lead.get("socios_json")
    if socios_json:
        try:
            if isinstance(socios_json, str):
                socios = json.loads(socios_json)
            else:
                socios = socios_json
            if isinstance(socios, list):
                # Procurar administrador/sócio-administrador
                for socio in socios:
                    qual = (socio.get("qualificacao") or "").lower()
                    if "administrador" in qual:
                        resultado["nome_dono"] = _formatar_nome(socio.get("nome", ""))
                        break
                # Fallback: primeiro sócio
                if not resultado["nome_dono"] and socios:
                    resultado["nome_dono"] = _formatar_nome(socios[0].get("nome", ""))
        except (json.JSONDecodeError, TypeError):
            pass

    # Contexto baseado em dados disponíveis
    nome_fantasia = lead.get("nome_fantasia") or lead.get("razao_social") or "seu restaurante"
    rating = lead.get("rating") or 0
    reviews = lead.get("total_reviews") or 0

    if rating > 0 and reviews > 0:
        resultado["contexto"] = (
            f"Vi que o {nome_fantasia} tem {rating} estrelas e "
            f"{reviews} avaliações no Google — parabéns!"
        )

    # Abordagem baseada em delivery
    tem_ifood = lead.get("tem_ifood") or 0
    tem_rappi = lead.get("tem_rappi") or 0
    tem_99food = lead.get("tem_99food") or 0
    total_delivery = tem_ifood + tem_rappi + tem_99food

    if total_delivery == 0:
        resultado["abordagem"] = (
            "Percebi que vocês ainda não estão em plataformas de delivery. "
            "A Derekh cria seu delivery próprio em 48h, sem comissões."
        )
    elif tem_ifood:
        resultado["abordagem"] = (
            "Vi que vocês já estão no iFood — a Derekh funciona como complemento. "
            "Delivery próprio, sem comissão de 27%."
        )
    else:
        resultado["abordagem"] = (
            "Vi que vocês já trabalham com delivery — a Derekh cria seu "
            "canal próprio para ter mais controle e margem."
        )

    # Tom baseado em porte
    porte = lead.get("porte") or ""
    mei = lead.get("mei") or ""
    if mei == "S" or "MEI" in porte.upper():
        resultado["tom"] = "informal"
    elif (lead.get("capital_social") or 0) >= 200000:
        resultado["tom"] = "formal"

    return resultado


def _formatar_nome(nome: str) -> str:
    """Formata nome para título (primeira letra maiúscula)."""
    if not nome:
        return ""
    return " ".join(p.capitalize() for p in nome.lower().split()[:2])


# ============================================================
# CÁLCULO EM BATCH
# ============================================================

def calcular_scores_todos(batch_size: int = 5000) -> dict:
    """Calcula e atualiza lead_score + segmento para todos os leads.
    Processa em batches para não sobrecarregar memória.
    Retorna estatísticas."""
    conn = _get_conn()
    stats = {"total": 0, "atualizados": 0, "por_segmento": {}}

    try:
        cur = conn.cursor()
        # Contar total
        cur.execute("SELECT COUNT(*) as total FROM leads")
        stats["total"] = cur.fetchone()["total"]

        offset = 0
        while True:
            cur.execute("""
                SELECT id, tem_ifood, tem_rappi, tem_99food,
                       rating, total_reviews, capital_social, porte,
                       data_abertura, email, telefone1,
                       status_pipeline, email_invalido,
                       multi_restaurante, socios_json, mei,
                       nome_fantasia, razao_social
                FROM leads
                ORDER BY id
                LIMIT %s OFFSET %s
            """, (batch_size, offset))

            rows = cur.fetchall()
            if not rows:
                break

            updates = []
            for lead in rows:
                score = calcular_score(lead)
                segmento = determinar_segmento(lead, score)
                updates.append((score, segmento, lead["id"]))
                stats["por_segmento"][segmento] = stats["por_segmento"].get(segmento, 0) + 1

            # Batch update
            cur.executemany("""
                UPDATE leads SET lead_score = %s, segmento = %s WHERE id = %s
            """, updates)
            conn.commit()

            stats["atualizados"] += len(updates)
            offset += batch_size

        return stats
    finally:
        conn.close()


def calcular_score_lead(lead_id: int) -> dict:
    """Calcula e atualiza score de um lead específico. Retorna score e segmento."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, tem_ifood, tem_rappi, tem_99food,
                   rating, total_reviews, capital_social, porte,
                   data_abertura, email, telefone1,
                   status_pipeline, email_invalido,
                   multi_restaurante, socios_json, mei,
                   nome_fantasia, razao_social
            FROM leads WHERE id = %s
        """, (lead_id,))
        lead = cur.fetchone()
        if not lead:
            return {"erro": "Lead não encontrado"}

        score = calcular_score(lead)
        segmento = determinar_segmento(lead, score)

        cur.execute("""
            UPDATE leads SET lead_score = %s, segmento = %s WHERE id = %s
        """, (score, segmento, lead_id))
        conn.commit()

        return {"lead_id": lead_id, "score": score, "segmento": segmento}
    finally:
        conn.close()
