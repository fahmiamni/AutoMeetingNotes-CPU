import os
import time
from pathlib import Path

import markdown
import requests
from dotenv import load_dotenv


load_dotenv()

MATON_BASE_URL = 'https://api.maton.ai'
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 5
BASE_DELAY_SECONDS = 2.0


def _maton_headers() -> dict:
    api_key = os.getenv('MATON_API_KEY')
    if not api_key:
        raise SystemExit(
            "Error: MATON_API_KEY not found.\n"
            "Set it in .env or as an environment variable."
        )
    return {
        'Authorization': f'Bearer {api_key}',
    }


def _maton_request(method: str, url: str, **kwargs) -> requests.Response:
    """Send a request to the Maton API, retrying on 429 and transient 5xx."""
    timeout = kwargs.pop('timeout', 30)
    for attempt in range(MAX_RETRIES):
        resp = requests.request(method, url, timeout=timeout, **kwargs)
        if resp.status_code not in RETRYABLE_STATUS_CODES:
            return resp

        if attempt < MAX_RETRIES - 1:
            retry_after = resp.headers.get('Retry-After')
            if retry_after and retry_after.isdigit():
                delay = float(retry_after)
            else:
                delay = BASE_DELAY_SECONDS * (2 ** attempt)
            delay = min(delay, 60.0)
            print(
                f"  Maton API {resp.status_code} on {url}; "
                f"retrying in {delay:.0f}s (attempt {attempt + 1}/{MAX_RETRIES})..."
            )
            time.sleep(delay)

    return resp


def _resolve_section_id() -> str:
    """Resolve ONENOTE_NOTEBOOK_NAME + ONENOTE_SECTION_NAME to a section ID."""
    notebook_name = os.getenv('ONENOTE_NOTEBOOK_NAME', '')
    section_name = os.getenv('ONENOTE_SECTION_NAME', '')

    if not notebook_name or not section_name:
        raise SystemExit(
            "Error: ONENOTE_NOTEBOOK_NAME and ONENOTE_SECTION_NAME must be set.\n"
            "Set them in .env or as environment variables."
        )

    headers = _maton_headers()

    # List notebooks
    resp = _maton_request('GET', f'{MATON_BASE_URL}/one-note/v1.0/me/onenote/notebooks', headers=headers)
    if resp.status_code != 200:
        raise SystemExit(f"Maton API error listing notebooks {resp.status_code}: {resp.text}")

    notebooks = resp.json().get('value', [])
    notebook = next((n for n in notebooks if n['displayName'] == notebook_name), None)
    if not notebook:
        available = [n['displayName'] for n in notebooks]
        raise SystemExit(
            f"Notebook '{notebook_name}' not found.\n"
            f"Available notebooks: {available}"
        )

    # List sections in the notebook (use Maton gateway URL, not raw Graph URL)
    notebook_id = notebook.get('id', '')
    sections_url = f'{MATON_BASE_URL}/one-note/v1.0/me/onenote/notebooks/{notebook_id}/sections'
    resp = _maton_request('GET', sections_url, headers=headers)
    if resp.status_code != 200:
        raise SystemExit(f"Maton API error listing sections {resp.status_code}: {resp.text}")

    sections = resp.json().get('value', [])
    section = next((s for s in sections if s['displayName'] == section_name), None)
    if not section:
        available = [s['displayName'] for s in sections]
        raise SystemExit(
            f"Section '{section_name}' not found in notebook '{notebook_name}'.\n"
            f"Available sections: {available}"
        )

    return section['id']


def _markdown_to_onenote_html(title: str, md_content: str) -> str:
    """Convert markdown content to OneNote-compatible HTML."""
    body_html = markdown.markdown(md_content, extensions=['tables', 'fenced_code'])
    return (
        '<!DOCTYPE html>\n'
        '<html>\n'
        '<head>\n'
        f'<title>{title}</title>\n'
        '</head>\n'
        '<body>\n'
        f'{body_html}\n'
        '</body>\n'
        '</html>'
    )


def save_to_onenote(content: str, title: str) -> str | None:
    """Save markdown content to OneNote via Maton API.

    Returns the page ID on success, None on failure (prints warning).
    """
    try:
        section_id = _resolve_section_id()
        html = _markdown_to_onenote_html(title, content)

        resp = _maton_request(
            'POST',
            f'{MATON_BASE_URL}/one-note/v1.0/me/onenote/sections/{section_id}/pages',
            headers={**_maton_headers(), 'Content-Type': 'text/html'},
            data=html.encode('utf-8'),
        )
        if resp.status_code not in (200, 201):
            print(f"  Warning: OneNote save failed ({resp.status_code}): {resp.text}")
            return None

        page = resp.json()
        page_id = page.get('id', 'unknown')
        print(f"  Saved to OneNote: {title} (page {page_id})")
        return page_id

    except requests.RequestException as e:
        print(f"  Warning: OneNote save failed (network error): {e}")
        return None
    except SystemExit as e:
        print(f"  Warning: OneNote save failed: {e}")
        return None


def save_notes(
    content: str,
    filename_stem: str,
    save_onenote: bool = True,
) -> None:
    """Save meeting notes to configured destinations."""
    if save_onenote:
        save_to_onenote(content, filename_stem)
