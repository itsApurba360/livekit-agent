# Vobiz Inbound Message

Tiny HTTP server for Vobiz inbound calls. It returns VobizXML to play custom audio files.

## Features

- **CORS & Preflight OPTIONS**: Handles browser CORS and Vobiz console validation (`OPTIONS` preflight) requests.
- **Dynamic Routing**: Uses the `Host` header to dynamically build XML absolute URLs.
- **Audio Formats Available**:
  - `/audio.mp3`: Compatibility-optimized MP3 file (played by default).
  - `/audio.wav`: Telephony-optimized downsampled **8 kHz, 16-bit, Mono PCM** WAV file.
  - `/audio_original.wav`: Original high-definition 24 kHz WAV file.

## Run locally

```bash
python server.py
curl -i http://127.0.0.1:8000/
```

## Dokploy Deployment

Deploy this folder as a Docker app:

### Build Type
* **Build type**: `dockerfile`
* **Dockerfile**: `vobizinbound/Dockerfile`
* **Context Path**: `vobizinbound`
* **Git Build Path**: `/`

### Vobiz Configuration
Set the Vobiz DID/Application Answer URL to:

```text
https://your-dokploy-domain/
```

Method: `POST`

Health check:

```text
/health
```
