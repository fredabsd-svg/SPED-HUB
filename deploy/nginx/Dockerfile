# Imagem do nginx com o que o entrypoint precisa.
#
# Existe por um motivo só: o entrypoint gera um certificado autoassinado quando
# não há Let's Encrypt, e para isso precisa do binário `openssl`.  A imagem
# oficial `nginx:alpine` traz as bibliotecas de TLS (o nginx as usa), mas não
# garante o utilitário de linha de comando — e depender disso em runtime seria
# apostar numa suposição que só quebraria no servidor do escritório.
#
# Instalar aqui também evita rede na subida do container: numa instalação nova,
# o primeiro `docker compose up` funciona sem baixar nada.
FROM nginx:1.25-alpine

RUN apk add --no-cache openssl

COPY proxy.conf /etc/nginx/proxy.conf
COPY entrypoint.sh /sped-hub-entrypoint.sh
RUN chmod +x /sped-hub-entrypoint.sh

ENTRYPOINT ["/sped-hub-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
