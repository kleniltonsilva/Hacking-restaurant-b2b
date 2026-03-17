"""Verificar estado dos socios no banco."""
import sqlite3
import json
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)

total = conn.execute("SELECT COUNT(*) FROM cnpjs_receita").fetchone()[0]
socios = conn.execute("SELECT COUNT(*) FROM cnpjs_receita WHERE socios_json IS NOT NULL AND socios_json != '[]'").fetchone()[0]
simples = conn.execute("SELECT COUNT(*) FROM cnpjs_receita WHERE simples IS NOT NULL").fetchone()[0]
det = conn.execute("SELECT COUNT(*) FROM cnpjs_receita WHERE detalhado = 1").fetchone()[0]

print(f"Total CNPJs:      {total:,}")
print(f"Com socios:       {socios:,}")
print(f"Simples preench:  {simples:,}")
print(f"Detalhado=1:      {det:,}")

# Mostrar exemplos de socios
rows = conn.execute(
    "SELECT cnpj, nome_fantasia, cidade, socios_json FROM cnpjs_receita "
    "WHERE socios_json IS NOT NULL AND socios_json != '[]' LIMIT 3"
).fetchall()

if rows:
    print("\n--- Exemplos de socios ---")
    for r in rows:
        print(f"\nCNPJ: {r[0]} | {r[1]} | {r[2]}")
        socios_data = json.loads(r[3])
        for s in socios_data:
            print(f"  -> {s['nome']} ({s['qualificacao']}) - {s['tipo']}")
else:
    print("\nNenhum socio encontrado ainda.")

# Controles
controles = conn.execute("SELECT chave, valor FROM controle_atualizacao").fetchall()
print(f"\nControles de atualizacao:")
for c in controles:
    print(f"  {c[0]}: {c[1]}")

conn.close()
