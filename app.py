import streamlit as st
import asyncio
import sys
import os
import tempfile
import subprocess
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
    # CSS approaches fail because Notion's own .callout p { margin:0 } rule
    # and the inline white-space:pre-wrap on the figure interact unpredictably
    # in Chromium's print renderer. Injecting inline styles here bypasses all
    # of that — inline styles on the element itself are applied unconditionally.
    for callout in soup.find_all(class_='callout'):
        # Strip white-space:pre-wrap from the figure's inline style so it
        # doesn't suppress paragraph spacing inside
        style = callout.get('style', '')
        for variant in ('white-space:pre-wrap;', 'white-space: pre-wrap;',
                        'white-space:pre-wrap', 'white-space: pre-wrap'):
            style = style.replace(variant, '')
        callout['style'] = style.strip()

        # Inject margin directly onto each <p> inside the callout
        paragraphs = callout.find_all('p')
        for i, p_tag in enumerate(paragraphs):
            existing = p_tag.get('style', '').rstrip(';')
            margin_top    = '0'     if i == 0                    else '0.6em'
            margin_bottom = '0'     if i == len(paragraphs) - 1  else '0.6em'
            p_tag['style'] = f"{existing}; margin-top:{margin_top}; margin-bottom:{margin_bottom};"

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
        page = await browser.new_page()

        await page.set_content(str(soup), wait_until="networkidle")

        # Strip inline pixel widths Notion injects directly onto td/th elements.
        # (The min-width class rule is handled by the CSS above.)
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

# --- Streamlit WebUI ---
st.set_page_config(page_title="Notion Engineering PDF Tool", page_icon="📑")

st.title("📑 Notion to Engineering PDF")

with st.sidebar:
    st.header("Report Configuration")
    header_input = st.text_input("Header Text", "EE22005: Engineering Practice and Design")
    footer_input = st.text_input("Footer Text", "Leo Wildman (ljrw20) - University of Bath")

uploaded_file = st.file_uploader("Upload Notion HTML", type=['html'])

if uploaded_file is not None:
    if st.button("Generate & Download PDF", type="primary"):
        if sys.platform != "win32":
            with st.spinner("Provisioning browser engine..."):
                try:
                    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
                except Exception as e:
                    st.error(f"Browser installation failed: {e}")

        with st.spinner("Rendering report..."):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp_html:
                tmp_html.write(uploaded_file.getvalue())
                tmp_path = tmp_html.name

            output_path = "notion_report.pdf"

            try:
                asyncio.run(generate_pdf(tmp_path, output_path, header_input, footer_input))

                with open(output_path, "rb") as f:
                    st.success("PDF Generation Complete!")
                    st.download_button(
                        label="Download PDF",
                        data=f,
                        file_name=f"{uploaded_file.name.replace('.html', '')}.pdf",
                        mime="application/pdf"
                    )
            except Exception as e:
                st.error(f"Processing Error: {e}")
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                if os.path.exists(output_path):
                    os.remove(output_path)