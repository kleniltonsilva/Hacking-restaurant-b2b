"""
competitor_service.py - Inteligência competitiva: busca concorrentes e dados de mercado
para personalizar emails com dados reais do mercado local do lead.
"""
from crm.database import get_conn


def dados_mercado_cidade(cidade: str, uf: str) -> dict:
    """Retorna estatísticas do mercado de restaurantes da cidade do lead."""
    with get_conn() as conn:
        cur = conn.cursor()

        stats = {}

        # Total de restaurantes na cidade
        cur.execute("""
            SELECT COUNT(*) as total FROM leads
            WHERE cidade = %s AND uf = %s
        """, (cidade, uf))
        stats["total_restaurantes"] = cur.fetchone()["total"]

        # Com delivery
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE tem_ifood = 1) as com_ifood,
                COUNT(*) FILTER (WHERE tem_rappi = 1) as com_rappi,
                COUNT(*) FILTER (WHERE tem_99food = 1) as com_99food,
                COUNT(*) FILTER (WHERE tem_ifood = 1 OR tem_rappi = 1 OR tem_99food = 1) as com_algum_delivery,
                COUNT(*) FILTER (WHERE COALESCE(tem_ifood,0)=0 AND COALESCE(tem_rappi,0)=0 AND COALESCE(tem_99food,0)=0) as sem_delivery
            FROM leads
            WHERE cidade = %s AND uf = %s
        """, (cidade, uf))
        row = cur.fetchone()
        stats.update(dict(row))

        # Rating médio (quando disponível)
        cur.execute("""
            SELECT ROUND(AVG(rating)::numeric, 1) as rating_medio,
                   ROUND(AVG(total_reviews)::numeric, 0) as reviews_medio
            FROM leads
            WHERE cidade = %s AND uf = %s AND rating > 0
        """, (cidade, uf))
        row = cur.fetchone()
        stats["rating_medio"] = row["rating_medio"] or 0
        stats["reviews_medio"] = int(row["reviews_medio"] or 0)

        # Distribuição por porte
        cur.execute("""
            SELECT porte, COUNT(*) as total
            FROM leads
            WHERE cidade = %s AND uf = %s AND porte IS NOT NULL
            GROUP BY porte
            ORDER BY total DESC
        """, (cidade, uf))
        stats["portes"] = {r["porte"]: r["total"] for r in cur.fetchall()}

        return stats


def concorrentes_bairro(lead_id: int, limite: int = 5) -> list:
    """Retorna concorrentes do mesmo bairro/cidade com delivery ativo."""
    with get_conn() as conn:
        cur = conn.cursor()

        # Buscar dados do lead
        cur.execute("SELECT bairro, cidade, uf FROM leads WHERE id = %s", (lead_id,))
        lead = cur.fetchone()
        if not lead or not lead["cidade"]:
            return []

        # Buscar concorrentes no mesmo bairro que TÊM delivery
        cur.execute("""
            SELECT nome_fantasia, razao_social, bairro,
                   tem_ifood, tem_rappi, tem_99food,
                   rating, total_reviews, nome_ifood
            FROM leads
            WHERE cidade = %s AND uf = %s
            AND id != %s
            AND (tem_ifood = 1 OR tem_rappi = 1 OR tem_99food = 1)
            ORDER BY
                CASE WHEN bairro = %s THEN 0 ELSE 1 END,
                rating DESC NULLS LAST
            LIMIT %s
        """, (lead["cidade"], lead["uf"], lead_id,
              lead.get("bairro") or "", limite))

        return [dict(r) for r in cur.fetchall()]


def concorrentes_cidade_top(cidade: str, uf: str, limite: int = 10) -> list:
    """Top restaurantes da cidade por rating/reviews (que têm delivery)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT nome_fantasia, razao_social, bairro,
                   tem_ifood, tem_rappi, tem_99food,
                   rating, total_reviews
            FROM leads
            WHERE cidade = %s AND uf = %s
            AND (tem_ifood = 1 OR tem_rappi = 1 OR tem_99food = 1)
            AND rating > 0
            ORDER BY rating DESC, total_reviews DESC
            LIMIT %s
        """, (cidade, uf, limite))
        return [dict(r) for r in cur.fetchall()]


def percentual_delivery_bairro(cidade: str, uf: str, bairro: str) -> dict:
    """Percentual de restaurantes com delivery no bairro."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE tem_ifood = 1 OR tem_rappi = 1 OR tem_99food = 1) as com_delivery
            FROM leads
            WHERE cidade = %s AND uf = %s AND bairro = %s
        """, (cidade, uf, bairro))
        row = cur.fetchone()
        total = row["total"] or 1
        com = row["com_delivery"] or 0
        return {
            "total": total,
            "com_delivery": com,
            "sem_delivery": total - com,
            "percentual": round(com / total * 100, 1) if total > 0 else 0,
        }
