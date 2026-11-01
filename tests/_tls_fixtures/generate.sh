#!/bin/sh
set -e
cd "$(dirname "$0")"

if [ -f server.pem ] && [ -f server.key ] && [ -f ca.pem ]; then
  exit 0
fi

# CA: basicConstraints CA:TRUE + keyUsage for signing
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout ca.key -out ca.pem \
  -subj "/CN=Agency Reliability Desk Test CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  2>/dev/null

# Server key + CSR
openssl req -newkey rsa:2048 -nodes \
  -keyout server.key -out server.csr \
  -subj "/CN=127.0.0.1" \
  2>/dev/null

# Server cert: SAN + basicConstraints + keyUsage + EKU
cat > server-ext.cnf <<EOF
subjectAltName=DNS:localhost,IP:127.0.0.1
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
EOF

openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial \
  -out server.pem -days 365 \
  -extfile server-ext.cnf \
  2>/dev/null

rm -f server.csr ca.srl server-ext.cnf
echo "TLS fixtures regenerated with keyUsage + EKU"
