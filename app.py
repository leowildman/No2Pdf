import streamlit as st
import asyncio
import sys
import os
import tempfile
import subprocess
import zipfile
import shutil
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

async def generate_pdf(input_html_path, output_pdf_path, header_text, footer_text):
    with open(input_html_path, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')

    notion_grey = "#91918e"

    custom_styles = f"""
    <style>
        /* Noto Color Emoji is loaded as a web font so emoji render correctly
           on Linux servers where the system emoji font is not installed */
        @import url('https://fonts.googleapis.com/css2?family=Noto+Color+Emoji&display=swap');

        @media print {{
            hr {{ break-before: page; visibility: hidden; height: 0; margin: 0 !important; }}

            /* ROOT CAUSE FIX:
               Notion's own stylesheet sets min-width: 120px on every .simple-table
               td and th. With 10 columns that forces a 1200px minimum table width,
               causing it to overflow the page. Override it to zero here. */
            .simple-table td,
            .simple-table th {{
                min-width: 0 !important;
                height: auto !important;
            }}

            table {{ 
                width: 100% !important; 
                max-width: 100% !important;
                table-layout: auto !important; 
                border-collapse: collapse !important;
            }}
            td, th {{
                border: 1px solid rgba(55, 53, 47, 0.09) !important;
                padding: 6px !important;
                /* Inherit body font size so table text matches body — same as Notion */
                white-space: pre-wrap !important; 
                word-wrap: break-word !important;
                overflow-wrap: break-word !important;
            }}
            tr {{ break-inside: avoid !important; }}

            .callout {{
                display: flex !important;
                padding: 16px !important;
                border-radius: 4px !important;
                align-items: flex-start !important;
            }}
            .icon, .notion-emoji {{ font-family: 'Noto Color Emoji', sans-serif !important; }}

            body {{ 
                /* Notion's actual font stack — system fonts render reliably
                   in Playwright without needing a network font load */
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif !important;
                color: rgb(55, 53, 47);
            }}
            img, figure {{ break-inside: avoid; max-width: 100% !important; }}
        }}
    </style>
    """
    if soup.head:
        soup.head.append(BeautifulSoup(custom_styles, 'html.parser'))

    # Fix callout paragraph spacing by preprocessing the HTML directly.
    for callout in soup.find_all(class_='callout'):
        style = callout.get('style', '')
        for variant in ('white-space:pre-wrap;', 'white-space: pre-wrap;',
                        'white-space:pre-wrap', 'white-space: pre-wrap'):
            style = style.replace(variant, '')
        callout['style'] = style.strip()

        paragraphs = callout.find_all('p')
        for i, p_tag in enumerate(paragraphs):
            existing = p_tag.get('style', '').rstrip(';')
            margin_top    = '0'    if i == 0                   else '0.6em'
            margin_bottom = '0'    if i == len(paragraphs) - 1 else '0.6em'
            p_tag['style'] = f"{existing}; margin-top:{margin_top}; margin-bottom:{margin_bottom};"

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
        page = await browser.new_page()

        # Load the HTML from its directory so relative image paths resolve correctly
        html_dir = os.path.dirname(os.path.abspath(input_html_path))
        await page.goto(f"file://{input_html_path}", wait_until="networkidle")

        # Inject our custom styles after page load
        await page.add_style_tag(content=custom_styles)

        # Apply callout fixes via JS (since we're using goto instead of set_content)
        await page.evaluate('''() => {
            document.querySelectorAll('.callout').forEach(callout => {
                callout.style.whiteSpace = 'normal';
            });
        }''')

        # Strip inline pixel widths Notion injects directly onto td/th elements.
        await page.evaluate('''() => {
            document.querySelectorAll('table, th, td, col, colgroup').forEach(el => {
                el.style.width = '';
                el.style.minWidth = '';
                el.style.maxWidth = '';
            });
        }''')

        shared_style = f"font-family: ui-sans-serif, -apple-system, sans-serif; font-size: 10px; color: {notion_grey};"

        await page.pdf(
            path=output_pdf_path,
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template=f'<div style="{shared_style} width: 100%; text-align: center; margin: 0 40px;">{header_text}</div>',
            footer_template=f'''
                <div style="{shared_style} width: 100%; padding: 0 40px; display: flex; justify-content: space-between;">
                    <span>{footer_text}</span>
                    <span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>
                </div>''',
            margin={"top": "80px", "bottom": "80px", "left": "40px", "right": "40px"}
        )
        await browser.close()


def extract_zip(zip_bytes, extract_dir):
    """Extract a Notion zip (including zip-within-zip) and return the HTML path."""
    zip_path = os.path.join(extract_dir, "upload.zip")
    with open(zip_path, 'wb') as f:
        f.write(zip_bytes)

    # Repeatedly extract any zip files found until none remain
    zips_to_extract = [zip_path]
    while zips_to_extract:
        for zp in zips_to_extract:
            with zipfile.ZipFile(zp, 'r') as z:
                z.extractall(extract_dir)
            os.remove(zp)
        # Check if extraction produced any more zip files
        zips_to_extract = [
            os.path.join(root, fname)
            for root, _, files in os.walk(extract_dir)
            for fname in files
            if fname.endswith('.zip')
        ]

    # Find the HTML file — Notion always produces exactly one
    for root, _, files in os.walk(extract_dir):
        for fname in files:
            if fname.endswith('.html'):
                return os.path.join(root, fname)
    return None


# --- Streamlit WebUI ---
st.set_page_config(page_title="Notion Engineering PDF Tool", page_icon="📑")

st.title("📑 Notion to Engineering PDF")

with st.sidebar:
    st.header("Report Configuration")
    header_input = st.text_input("Header Text", "EE22005: Engineering Practice and Design")
    footer_input = st.text_input("Footer Text", "Leo Wildman (ljrw20) - University of Bath")

uploaded_file = st.file_uploader("Upload Notion HTML or ZIP", type=['html', 'zip'])

if uploaded_file is not None:
    if st.button("Generate & Download PDF", type="primary"):
        if sys.platform != "win32":
            with st.spinner("Provisioning browser engine..."):
                try:
                    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
                except Exception as e:
                    st.error(f"Browser installation failed: {e}")

        with st.spinner("Rendering report..."):
            # Use a persistent temp dir so images remain accessible during render
            tmp_dir = tempfile.mkdtemp()
            output_path = os.path.join(tmp_dir, "notion_report.pdf")

            try:
                if uploaded_file.name.endswith('.zip'):
                    html_path = extract_zip(uploaded_file.getvalue(), tmp_dir)
                    if html_path is None:
                        st.error("No HTML file found inside the ZIP.")
                        st.stop()
                    output_name = os.path.basename(html_path).replace('.html', '') + ".pdf"
                else:
                    html_path = os.path.join(tmp_dir, uploaded_file.name)
                    with open(html_path, 'wb') as f:
                        f.write(uploaded_file.getvalue())
                    output_name = uploaded_file.name.replace('.html', '') + ".pdf"

                asyncio.run(generate_pdf(html_path, output_path, header_input, footer_input))

                with open(output_path, "rb") as f:
                    st.success("PDF Generation Complete!")
                    st.download_button(
                        label="Download PDF",
                        data=f,
                        file_name=output_name,
                        mime="application/pdf"
                    )
            except Exception as e:
                st.error(f"Processing Error: {e}")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)