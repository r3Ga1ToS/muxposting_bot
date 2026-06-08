# Telegram Auto Layout Post Maker Bot

A robust Telegram automation bot designed to bulk upload Google Drive links to HubCloud and GDFlix, parse APIs (bypassing Cloudflare protection), and generate formatted layout posts natively for Telegram channels.

## Features
- **Cloudflare Bypass**: Utilizes `curl_cffi` to spoof Chrome TLS fingerprints.
- **Dynamic Domains**: Admins can change redirector domains on the fly via `/chngd`.
- **Multi-API Syncing**: Share batches of files to multiple services simultaneously.
- **Clean Layouts**: Generates highly formatted markdown posts with hidden poster images and standardized layout blocks.
- **Error Tracking**: Prevents layout breakage on API failures and returns detailed diagnostic reports.

## Setup Instructions

### 1. Clone & Install Dependencies
First, clone the repository to your server and install the Python requirements:

```bash
git clone <your-repo-link>
cd <your-repo-directory>
pip install -r requirements.txt
