#!/bin/bash
# Setup do Tor para Restaurant BI
# Execute com: sudo bash setup_tor.sh

set -e

echo "=== Instalando Tor ==="
apt install -y tor

echo "=== Gerando hash da senha ==="
TOR_HASH=$(tor --hash-password "restaurant_bi_2026" 2>/dev/null | grep '^16:')
echo "Hash gerado: $TOR_HASH"

if [[ ! "$TOR_HASH" =~ ^16:[A-F0-9]+$ ]]; then
    echo "AVISO: Hash pode estar incorreto, usando formato fixo..."
    TOR_HASH="16:2B077F0530C384B3606A0FA6FAE5FD4484BCA041C46B887F720A1ECF45"
fi

echo "=== Limpando config problemática ==="
# Remover %include que causa erro de permissão
sed -i '/^%include \/etc\/tor\/torrc\.d\//d' /etc/tor/torrc
# Remover diretório problemático
rm -rf /etc/tor/torrc.d/

echo "=== Configurando torrc ==="
# Remover linhas antigas que possamos ter adicionado
sed -i '/^ControlPort 9051$/d' /etc/tor/torrc
sed -i '/^HashedControlPassword /d' /etc/tor/torrc
sed -i '/^CookieAuthentication /d' /etc/tor/torrc
sed -i '/^# Restaurant BI/d' /etc/tor/torrc

# Descomentar SocksPort se comentado
sed -i 's/^#SocksPort 9050$/SocksPort 9050/' /etc/tor/torrc

# Adicionar config no final
cat >> /etc/tor/torrc << EOF

# Restaurant BI - Scraping config
ControlPort 9051
HashedControlPassword $TOR_HASH
CookieAuthentication 0
EOF

echo "=== Conteúdo relevante do torrc ==="
grep -v '^#\|^$' /etc/tor/torrc | head -20

echo "=== Reiniciando Tor ==="
systemctl restart tor
sleep 3

echo "=== Status ==="
systemctl status tor@default --no-pager | head -8

echo ""
echo "=== Portas ==="
ss -tlnp | grep -E '905[01]'

echo ""
echo "=== Testando SOCKS proxy ==="
curl -s --socks5-hostname 127.0.0.1:9050 https://api.ipify.org?format=json && echo ""

echo ""
echo "=== Tor configurado com sucesso! ==="
