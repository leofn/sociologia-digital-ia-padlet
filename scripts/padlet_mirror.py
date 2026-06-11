#!/usr/bin/env python3
"""
Padlet Mirror Script
Varre a API do Padlet por mudanças e sincroniza com o repositório GitHub.
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

WALL_HASHID = "board_N1gMAXd44b0lA2G7"
API_BASE = f"https://padlet.com/api/10/wishes?wall_hashid={WALL_HASHID}"
REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "dados"
SECOES_DIR = REPO_DIR / "secoes"
ARQUIVOS_DIR = REPO_DIR / "arquivos"
RAW_DATA_PATH = DATA_DIR / "padlet_raw_data.json"

SECTION_MAP = {
    193137542: "Links",
    346545343: "Textos Obrigatórios 2026",
    269401715: "Textos obrigatórios 2025",
    199427483: "Textos Obrigatórios (2023-2024)",
    193948276: "Textos Obrigatórios (2019-2022)",
    199432535: "Textos Complementares",
    193948237: "PPTs das apresentações",
    193950543: "Leituras futuras",
}

SECTION_ORDER = [
    (193137542, "Links", "01"),
    (346545343, "Textos Obrigatórios 2026", "02"),
    (269401715, "Textos obrigatórios 2025", "03"),
    (199427483, "Textos Obrigatórios (2023-2024)", "04"),
    (193948276, "Textos Obrigatórios (2019-2022)", "05"),
    (199432535, "Textos Complementares", "06"),
    (193948237, "PPTs das apresentações", "07"),
    (193950543, "Leituras futuras", "08"),
]


def fetch_all_wishes():
    """Busca todos os wishes via API com paginação."""
    all_wishes = []
    page_start = ""
    page = 1

    while True:
        url = f"{API_BASE}&page_start={page_start}" if page_start else f"{API_BASE}&page_start="
        print(f"  Buscando página {page}...")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"  ERRO ao buscar página {page}: {e}")
            break

        wishes = data.get("data", [])
        if not wishes:
            break

        all_wishes.extend(wishes)
        print(f"  → {len(wishes)} wishes na página {page}")

        next_page = data.get("meta", {}).get("next_page")
        if not next_page:
            break
        page_start = next_page
        page += 1
        if page > 20:  # safety limit
            break

    return all_wishes


def load_existing_data():
    """Carrega dados existentes do JSON."""
    if RAW_DATA_PATH.exists():
        with open(RAW_DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def find_changes(existing, new):
    """Compara wishes existentes com novos e retorna mudanças."""
    existing_ids = {w["id"] for w in existing}
    new_ids = {w["id"] for w in new}

    added = [w for w in new if w["id"] not in existing_ids]
    removed = [w for w in existing if w["id"] not in new_ids]

    # Check for edits (same ID, different content)
    existing_map = {w["id"]: w for w in existing}
    new_map = {w["id"]: w for w in new}
    edited = []
    common_ids = existing_ids & new_ids
    for wid in common_ids:
        if json.dumps(existing_map[wid], sort_keys=True) != json.dumps(new_map[wid], sort_keys=True):
            edited.append(new_map[wid])

    return added, removed, edited


def download_file(url, out_path):
    """Baixa um arquivo via curl."""
    try:
        result = subprocess.run(
            ["curl", "-sL", "-o", str(out_path), "--max-time", "120", "--retry", "2",
             "-H", "User-Agent: Mozilla/5.0", url],
            capture_output=True, timeout=180
        )
        if out_path.exists() and out_path.stat().st_size > 100:
            return True
        if out_path.exists():
            out_path.unlink()
    except Exception:
        pass
    return False


def detect_extension(header_bytes):
    """Detecta extensão real pelo magic bytes."""
    if header_bytes[:4] == b"%PDF":
        return ".pdf"
    if header_bytes[:4] == b"PK\x03\x04":
        # ZIP-based - check deeper
        return None  # Will check with zipfile
    if header_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if header_bytes[:3] == b"\xff\xd8\xff":
        return ".jpg"
    return None


def fix_extension(filepath):
    """Corrige extensão baseada no conteúdo real do arquivo."""
    import zipfile
    try:
        with open(filepath, "rb") as f:
            header = f.read(32)

        ext = detect_extension(header)
        if ext:
            return ext

        # ZIP-based formats
        if header[:4] == b"PK\x03\x04":
            try:
                with zipfile.ZipFile(filepath) as zf:
                    names = zf.namelist()
                    if any("ppt/" in n or "presentation.xml" in n for n in names):
                        return ".pptx"
                    elif any("word/" in n or "document.xml" in n for n in names):
                        return ".docx"
                    elif any("mimetype" in n for n in names):
                        with zf.open([n for n in names if "mimetype" in n][0]) as mf:
                            mt = mf.read().decode()
                            if "epub" in mt:
                                return ".epub"
                            elif "opendocument" in mt:
                                return ".odt"
            except:
                pass
    except:
        pass
    return None


def download_new_files(added_wishes):
    """Baixa arquivos dos novos posts."""
    downloaded = 0
    for w in added_wishes:
        attrs = w.get("attributes", {})
        sid = attrs.get("wall_section_id")
        section = SECTION_MAP.get(sid, "Unknown")
        safe_section = re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-")
        headline = attrs.get("headline", "") or ""
        if headline == "Vazio":
            headline = ""

        # Collect file URLs
        file_urls = []
        att_link = attrs.get("attachment_link") or {}
        if isinstance(att_link, dict):
            url = att_link.get("canonical_url", "") or att_link.get("display_url", "")
            if url and ("padlet-uploads" in url or "storage.googleapis" in url or "padletusercontent" in url):
                file_urls.append(url)

        wc = attrs.get("wish_content") or {}
        if isinstance(wc, dict):
            ap = wc.get("attachment_props") or {}
            if isinstance(ap, dict):
                url = ap.get("url", "") or ap.get("signed_url", "")
                if url:
                    file_urls.append(url)

        att = attrs.get("attachment")
        if isinstance(att, dict):
            url = att.get("url", "") or att.get("canonical_url", "")
            if url and url not in file_urls:
                file_urls.append(url)

        outdir = ARQUIVOS_DIR / safe_section
        outdir.mkdir(parents=True, exist_ok=True)

        for url in file_urls:
            # Build filename from URL
            from urllib.parse import urlparse, unquote
            parsed = urlparse(url)
            path = unquote(parsed.path)
            parts = path.split("/")
            filename = parts[-1] if parts[-1] else parts[-2] if len(parts) > 1 else "file"
            filename = filename.split("?")[0]
            filename = re.sub(r'[\\/:*?"<>|]', '_', filename)

            if headline:
                safe_headline = re.sub(r'[\\/:*?"<>|\n]', ' ', headline).strip()[:60]
                filename = f"{safe_headline} - {filename}"

            out_path = outdir / filename

            if download_file(url, out_path):
                # Fix extension if needed
                real_ext = fix_extension(out_path)
                current_ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
                if real_ext and current_ext.lower() != real_ext.lstrip("."):
                    new_path = Path(str(out_path).rsplit(".", 1)[0] + real_ext)
                    out_path.rename(new_path)
                downloaded += 1

    return downloaded


def generate_section_md(wishes, section_id, section_name, section_num):
    """Gera o markdown de uma seção."""
    section_wishes = [w for w in wishes if w.get("attributes", {}).get("wall_section_id") == section_id]
    section_wishes.sort(key=lambda w: w.get("attributes", {}).get("sort_index", 0))

    lines = [f"# {section_name}\n"]
    lines.append(f"*{len(section_wishes)} posts*\n")

    for w in section_wishes:
        attrs = w.get("attributes", {})
        headline = attrs.get("headline", "") or attrs.get("subject", "") or ""
        if headline == "Vazio":
            headline = ""
        body = (attrs.get("body", "") or "").strip()
        permalink = attrs.get("permalink", "")
        created = attrs.get("created_at", "")
        author = attrs.get("author", {}).get("name", "") if isinstance(attrs.get("author"), dict) else ""

        # Collect links
        links = []
        att_link = attrs.get("attachment_link") or {}
        if isinstance(att_link, dict):
            url = att_link.get("canonical_url", "") or att_link.get("display_url", "")
            title = att_link.get("title", "")
            if url:
                links.append((url, title))

        wc = attrs.get("wish_content") or {}
        if isinstance(wc, dict):
            ap = wc.get("attachment_props") or {}
            if isinstance(ap, dict):
                url = ap.get("url", "") or ap.get("signed_url", "")
                fn = ap.get("filename", "")
                if url:
                    links.append((url, fn))

        att = attrs.get("attachment")
        if isinstance(att, dict):
            url = att.get("url", "") or att.get("canonical_url", "")
            fn = att.get("filename", "")
            if url and url not in [l[0] for l in links]:
                links.append((url, fn))

        # Format
        if headline:
            lines.append(f"## {headline}\n")
        if author:
            lines.append(f"*Autor: {author}*  ")
        if created:
            lines.append(f"*Data: {created[:10]}*  ")
        if body:
            lines.append(f"\n{body}\n")
        if links:
            lines.append("\n**Links/Arquivos:**")
            for url, title in links:
                label = title if title else url[:80]
                lines.append(f"- [{label}]({url})")
        if permalink:
            lines.append(f"\n[Ver no Padlet]({permalink})")
        lines.append("\n---\n")

    return "\n".join(lines)


def generate_links_md(all_wishes):
    """Gera o arquivo consolidado de links."""
    lines = ["# Links e Arquivos Consolidados\n"]

    seen_urls = set()
    for w in all_wishes:
        attrs = w.get("attributes", {})
        headline = attrs.get("headline", "") or ""
        sid = attrs.get("wall_section_id")
        section = SECTION_MAP.get(sid, "Unknown")

        urls = []
        att_link = attrs.get("attachment_link") or {}
        if isinstance(att_link, dict):
            url = att_link.get("canonical_url", "") or att_link.get("display_url", "")
            if url:
                urls.append(url)
        wc = attrs.get("wish_content") or {}
        if isinstance(wc, dict):
            ap = wc.get("attachment_props") or {}
            if isinstance(ap, dict):
                url = ap.get("url", "") or ap.get("signed_url", "")
                if url:
                    urls.append(url)
        att = attrs.get("attachment")
        if isinstance(att, dict):
            url = att.get("url", "") or att.get("canonical_url", "")
            if url:
                urls.append(url)

        for url in urls:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if headline:
                lines.append(f"- [{section}] {headline}: {url}")
            else:
                lines.append(f"- [{section}] {url}")

    return "\n".join(lines)


def update_readme(total_wishes):
    """Atualiza o README."""
    section_counts = {}
    for sid, sname, _ in SECTION_ORDER:
        section_counts[sname] = 0
    for w in total_wishes:
        sid = w.get("attributes", {}).get("wall_section_id")
        sname = SECTION_MAP.get(sid, "Unknown")
        section_counts[sname] = section_counts.get(sname, 0) + 1

    lines = [
        "# Sociologia Digital e Inteligência Artificial\n",
        f"Mirror do Padlet do grupo de pesquisa **Sociologia Digital e Inteligência Artificial**, organizado por Marcus Repa.\n",
        f"**Fonte original:** https://padlet.com/marcusrepa/sociologia-digital-e-inteligencia-artificial-cfn1emqp0om5xwr\n",
        f"**Total de posts:** {total_wishes} | **Seções:** {len(SECTION_MAP)}\n",
        "---\n\n## Sumário\n",
    ]

    for sid, sname, num in SECTION_ORDER:
        count = section_counts.get(sname, 0)
        lines.append(f"{num}. [{sname}](secoes/{num}-{re.sub(r'[^a-z0-9]+', '-', sname.lower()).strip('-')}.md) — {count} posts")

    lines.append("\n---\n\n## Estrutura\n")
    lines.append("- `secoes/` — Um arquivo `.md` por seção do Padlet")
    lines.append("- `arquivos/` — Arquivos baixados (PDFs, PPTXs, DOCXs etc.)")
    lines.append("- `links.md` — Lista consolidada de todos os links e arquivos")
    lines.append("- `dados/padlet_raw_data.json` — Dados brutos da API do Padlet (JSON)")
    lines.append("\n---\n\n*Atualizado automaticamente via GitHub Actions.*")

    return "\n".join(lines)


def main():
    print("=" * 60)
    print(f"Padlet Mirror — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # 1. Fetch current data
    print("\n1. Buscando dados do Padlet...")
    new_wishes = fetch_all_wishes()
    print(f"   Total: {len(new_wishes)} wishes")

    if not new_wishes:
        print("   ERRO: Nenhum dado recebido da API. Abortando.")
        sys.exit(1)

    # 2. Load existing data
    print("\n2. Carregando dados existentes...")
    existing_wishes = load_existing_data()
    print(f"   Existem: {len(existing_wishes)} wishes salvos")

    # 3. Compare
    print("\n3. Comparando mudanças...")
    added, removed, edited = find_changes(existing_wishes, new_wishes)
    print(f"   Novos: {len(added)} | Removidos: {len(removed)} | Editados: {len(edited)}")

    if not added and not removed and not edited:
        print("\n✅ Nenhuma mudança detectada no Padlet.")
        return

    # 4. Download new files
    print(f"\n4. Baixando {len(added)} novos posts...")
    downloaded = download_new_files(added)
    print(f"   {downloaded} arquivos baixados")

    # 5. Save raw data
    print("\n5. Salvando dados brutos...")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(RAW_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(new_wishes, f, ensure_ascii=False, indent=2)

    # 6. Regenerate section markdowns
    print("\n6. Regenerando markdowns das seções...")
    SECOES_DIR.mkdir(parents=True, exist_ok=True)
    for sid, sname, num in SECTION_ORDER:
        safe_name = re.sub(r"[^a-z0-9]+", "-", sname.lower()).strip("-")
        md = generate_section_md(new_wishes, sid, sname, num)
        out_path = SECOES_DIR / f"{num}-{safe_name}.md"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
        section_wishes = [w for w in new_wishes if w.get("attributes", {}).get("wall_section_id") == sid]
        print(f"   {num}-{safe_name}.md — {len(section_wishes)} posts")

    # 7. Update links.md
    print("\n7. Atualizando links.md...")
    links_md = generate_links_md(new_wishes)
    with open(REPO_DIR / "links.md", "w", encoding="utf-8") as f:
        f.write(links_md)

    # 8. Update README
    print("\n8. Atualizando README.md...")
    readme = update_readme(len(new_wishes))
    with open(REPO_DIR / "README.md", "w", encoding="utf-8") as f:
        f.write(readme)

    # 9. Git commit and push
    print("\n9. Commit e push...")
    subprocess.run(["git", "add", "-A"], cwd=REPO_DIR, capture_output=True)
    result = subprocess.run(
        ["git", "diff", "--cached", "--stat"],
        cwd=REPO_DIR, capture_output=True, text=True
    )
    if not result.stdout.strip():
        print("   Nenhuma mudança para commit.")
        return

    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit_msg = f"Atualização automática — {date_str}\n\nNovos: {len(added)} | Removidos: {len(removed)} | Editados: {len(edited)} | Arquivos baixados: {downloaded}"
    subprocess.run(["git", "commit", "-m", commit_msg], cwd=REPO_DIR, capture_output=True)
    result = subprocess.run(["git", "push"], cwd=REPO_DIR, capture_output=True, text=True)

    if result.returncode == 0:
        print("   ✅ Push com sucesso!")
    else:
        print(f"   ❌ Erro no push: {result.stderr}")

    print(f"\n{'=' * 60}")
    print(f"Resumo: +{len(added)} -{len(removed)} ~{len(edited)} | {downloaded} arquivos baixados")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()