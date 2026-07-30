#!/bin/sh
# Escolhe o certificado e o comportamento do HTTP ANTES de o nginx subir.
#
# O problema que isto resolve: o `nginx.conf` apontava direto para
# `/etc/letsencrypt/live/<dominio>/fullchain.pem`.  Numa instalação nova esse
# arquivo não existe, o nginx recusa subir, e o container entra em laço de
# reinício:
#
#   nginx: [emerg] cannot load certificate ".../fullchain.pem"
#   sped-hub-nginx exited with code 1 (restarting)
#
# E não havia como sair do laço: o certbot do compose só roda `renew`, que não
# emite nada na primeira vez, e a emissão precisa do nginx já servindo
# `/.well-known/acme-challenge/` na porta 80.  Ovo e galinha — `docker compose
# up` não funcionava na primeira execução para ninguém.
set -eu

DOMINIO="${SPED_HUB_DOMINIO:-sped-hub}"
DIR_REAL="/etc/letsencrypt/live/${DOMINIO}"
DIR_LOCAL="/etc/nginx/ssl"
CONF_CERT="${DIR_LOCAL}/certificado.conf"
CONF_HTTP="${DIR_LOCAL}/http-raiz.conf"

mkdir -p "$DIR_LOCAL"

if [ -r "${DIR_REAL}/fullchain.pem" ] && [ -r "${DIR_REAL}/privkey.pem" ]; then
    certificado="${DIR_REAL}/fullchain.pem"
    chave="${DIR_REAL}/privkey.pem"
    # Com certificado válido, o HTTP existe só para o desafio do ACME e para
    # empurrar todo mundo para o HTTPS.
    printf 'return 301 https://$host$request_uri;\n' > "$CONF_HTTP"
    echo "[sped-hub] certificado Let's Encrypt encontrado para '${DOMINIO}'; HTTP redireciona para HTTPS."
else
    certificado="${DIR_LOCAL}/autoassinado.pem"
    chave="${DIR_LOCAL}/autoassinado.key"
    if [ ! -r "$certificado" ] || [ ! -r "$chave" ]; then
        openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
            -keyout "$chave" -out "$certificado" \
            -subj "/CN=${DOMINIO}" >/dev/null 2>&1
        echo "[sped-hub] certificado autoassinado gerado em ${certificado}"
    fi
    # SEM certificado real, o HTTP serve a aplicação em vez de redirecionar:
    # mandar o navegador para um HTTPS autoassinado só produz aviso de
    # segurança, sem nenhum ganho.  É o caminho de quem está experimentando na
    # própria máquina.
    printf 'include /etc/nginx/proxy.conf;\n' > "$CONF_HTTP"
    echo "[sped-hub] AVISO: sem certificado para '${DOMINIO}'."
    echo "[sped-hub] Subindo com certificado AUTOASSINADO — bom para experimentar, NÃO para produção."
    echo "[sped-hub] A aplicação responde em http://localhost/  (HTTP, sem redirecionamento)."
    echo "[sped-hub] Para emitir o certificado real, ver 'SSL' em docs/deploy.md."
fi

printf 'ssl_certificate     %s;\nssl_certificate_key %s;\n' "$certificado" "$chave" > "$CONF_CERT"

# Entrega ao entrypoint da imagem oficial, que ainda roda os scripts de
# /docker-entrypoint.d/ antes de subir o nginx.
exec /docker-entrypoint.sh "$@"
