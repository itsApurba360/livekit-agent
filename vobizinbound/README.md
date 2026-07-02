# Vobiz Inbound Message

Tiny HTTP server for Vobiz inbound calls. It returns VobizXML for `GET` or `POST`.

## Run locally

```bash
python server.py
curl -i http://127.0.0.1:8000/
```

## Dokploy

Deploy this folder as a Docker app:

```text
vobizinbound/
```

Set the Vobiz DID/Application Answer URL to:

```text
https://your-dokploy-domain/
```

Method: `POST`

Health check:

```text
/health
```
