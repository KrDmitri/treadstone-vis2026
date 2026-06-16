# Treadstone

Treadstone is a collaborative data analysis workspace where users and AI agents
work together through a feed-style interface. Users can upload datasets, ask
questions, request visualizations, and continue an analysis as a discussion.
Specialized agents contribute statistical analysis, visual explanations,
contextual interpretation, summaries, and proactive follow-up ideas as the
conversation evolves.

This repository contains the FastAPI backend and React/Vite frontend needed to
run Treadstone locally.

## Prerequisites

- Python 3.10+
- Node.js 22+
- An OpenAI API key

## Environment Setup

Create a local environment file from the provided template:

```bash
cp env.example .env
```

Edit `.env` and set:

```bash
OPENAI_API_KEY=sk-your-api-key-here
```

The default backend port is `9000`, and the default frontend port is `3000`.

## Run the Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

The backend will run at:

```text
http://localhost:9000
```

## Run the Frontend

In a separate terminal:

```bash
cd frontend
npm install
npm run dev
```

The frontend will run at:

```text
http://localhost:3000
```

By default, the Vite development server proxies API and WebSocket requests to
the backend at `http://localhost:9000`.

## Optional Frontend Configuration

If the backend is running somewhere else, set these values before starting the
frontend:

```bash
VITE_API_URL=http://localhost:9000
VITE_WS_URL=ws://localhost:9000
```

## Basic Usage

1. Start the backend.
2. Start the frontend.
3. Open `http://localhost:3000`.
4. Upload a supported file, such as a CSV dataset.
5. Create a post or ask a data-analysis question to trigger agent responses.

Uploaded files and local session data are stored under the backend upload
directory during local development.

## Contact
Please contact sbcho98@postech.ac.kr if there is any issue within the code.
