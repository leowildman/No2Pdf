import streamlit as st
import asyncio
import sys
import os
import tempfile
import subprocess
import zipfile
import shutil
from datetime import date
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

async def generate_pdf(
    input_html_path, output_pdf_path,
    header_left, header_centre, header_right,
    footer_left, footer_right,
    suppress_first_page_hf,
    page_size, landscape,
    margin_top, margin_bottom, margin_left, margin_right,
    body_font_size, table_font_size, line_height,
    pdf_title, pdf_author,
):
    with open(input_html_path, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')

    # Inject PDF metadata
    if pdf_title:
        tag = soup.new_tag('meta'); tag['name'] = 'title'; tag['content'] = pdf_title
        soup.head.append(tag)
    if pdf_author:
        tag = soup.new_tag('meta'); tag['name'] = 'author'; tag['content'] = pdf_author
        soup.head.append(tag)

    notion_grey = "#91918e"

    # Only inject font-size/line-height overrides if the user has changed
    # from the "Notion default" sentinel values (0 = inherit)
    body_font_css  = f"font-size: {body_font_size}pt !important;" if body_font_size  > 0 else ""
    table_font_css = f"font-size: {table_font_size}pt !important;" if table_font_size > 0 else ""
    line_height_css = f"line-height: {line_height} !important;" if line_height > 0 else ""

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
                {table_font_css}
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
                {body_font_css}
                {line_height_css}
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
            mt = '0'    if i == 0                   else '0.6em'
            mb = '0'    if i == len(paragraphs) - 1 else '0.6em'
            p_tag['style'] = f"{existing}; margin-top:{mt}; margin-bottom:{mb};"

    # Write the modified soup back to disk in the same directory as the
    # original so that relative image paths (./images/...) still resolve.
    modified_html_path = input_html_path + ".modified.html"
    with open(modified_html_path, 'w', encoding='utf-8') as f:
        f.write(str(soup))

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
        page = await browser.new_page()

        # goto with file:// loads our modified file from the same directory
        # as the original so relative image paths resolve correctly
        await page.goto(f"file://{modified_html_path}", wait_until="networkidle")

        # Strip inline pixel widths Notion injects directly onto td/th elements.
        await page.evaluate('''() => {
            document.querySelectorAll('table, th, td, col, colgroup').forEach(el => {
                el.style.width = '';
                el.style.minWidth = '';
                el.style.maxWidth = '';
            });
        }''')

        # Prevent tables that fit on a single page from splitting across pages.
        # Compute printable height from page size and margins.
        page_heights_mm = {"A4": 297, "Letter": 279, "A3": 420}
        page_widths_mm  = {"A4": 210, "Letter": 216, "A3": 297}
        h_mm = page_widths_mm[page_size] if landscape else page_heights_mm[page_size]
        printable_h_px = (h_mm - margin_top - margin_bottom) * (96 / 25.4)

        await page.evaluate(f'''() => {{
            const MAX_H = {printable_h_px:.1f};
            document.querySelectorAll('table').forEach(table => {{
                if (table.getBoundingClientRect().height <= MAX_H) {{
                    table.style.breakInside = 'avoid';
                    table.style.pageBreakInside = 'avoid';
                }}
            }});
        }}''')

        # Build header/footer templates with left/centre/right slots
        shared_style = f"font-family: ui-sans-serif, -apple-system, sans-serif; font-size: 10px; color: {notion_grey}; box-sizing: border-box;"
        ml = f"{margin_left}mm"
        mr = f"{margin_right}mm"

        def three_col(left, centre, right):
            return (
                f'<div style="{shared_style} width:100%; padding:0 {mr} 0 {ml}; '
                f'display:flex; justify-content:space-between; align-items:center;">'
                f'<span style="flex:1; text-align:left;">{left}</span>'
                f'<span style="flex:1; text-align:center;">{centre}</span>'
                f'<span style="flex:1; text-align:right;">{right}</span>'
                f'</div>'
            )

        page_num = 'Page <span class="pageNumber"></span> of <span class="totalPages"></span>'
        footer_right_full = f"{footer_right} | {page_num}" if footer_right.strip() else page_num

        header_html = three_col(header_left, header_centre, header_right)
        footer_html  = three_col(footer_left, "", footer_right_full)

        # Suppress header/footer on page 1 by covering the margin area with
        # a white fixed-position block — only affects the first page since
        # subsequent pages scroll past it.
        if suppress_first_page_hf:
            await page.evaluate(f'''() => {{
                const s = document.createElement('style');
                s.textContent = `@media print {{
                    .hf-suppress {{ position:fixed; left:0; right:0; background:white; z-index:99999; }}
                    .hf-suppress-top    {{ top:0;    height:{margin_top}mm; }}
                    .hf-suppress-bottom {{ bottom:0; height:{margin_bottom}mm; }}
                }}`;
                document.head.appendChild(s);
                const t = document.createElement('div');
                t.className = 'hf-suppress hf-suppress-top';
                const b = document.createElement('div');
                b.className = 'hf-suppress hf-suppress-bottom';
                document.body.prepend(t);
                document.body.appendChild(b);
            }}''')

        await page.pdf(
            path=output_pdf_path,
            format=page_size,
            landscape=landscape,
            print_background=True,
            display_header_footer=True,
            header_template=header_html,
            footer_template=footer_html,
            margin={
                "top":    f"{margin_top}mm",
                "bottom": f"{margin_bottom}mm",
                "left":   f"{margin_left}mm",
                "right":  f"{margin_right}mm",
            },
        )
        await browser.close()

    if os.path.exists(modified_html_path):
        os.remove(modified_html_path)


def extract_zip(zip_bytes, extract_dir):
    """Extract a Notion zip (including zip-within-zip) and return the HTML path."""
    zip_path = os.path.join(extract_dir, "upload.zip")
    with open(zip_path, 'wb') as f:
        f.write(zip_bytes)

    # Repeatedly extract until no more zips remain (handles nested zips)
    zips_to_extract = [zip_path]
    while zips_to_extract:
        for zp in zips_to_extract:
            with zipfile.ZipFile(zp, 'r') as z:
                z.extractall(extract_dir)
            os.remove(zp)
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

today = date.today().strftime("%-d %B %Y") if sys.platform != "win32" else date.today().strftime("%d %B %Y")

with st.sidebar:
    st.header("Header")
    header_left_input   = st.text_input("Left",   "", key="hl")
    header_centre_input = st.text_input("Centre", "EE22005: Engineering Practice and Design", key="hc")
    header_right_input  = st.text_input("Right",  "", key="hr")

    st.header("Footer")
    footer_left_input  = st.text_input("Left",  "Username - University of Bath", key="fl")
    footer_right_input = st.text_input("Right (page number auto-appended)", "", key="fr")
    suppress_hf_p1     = st.toggle("Suppress on page 1", value=False,
                                    help="Hides header & footer on the cover page.")

    st.divider()
    st.header("Layout")
    page_size_input = st.selectbox("Page size", ["A4", "Letter", "A3"], index=0)
    landscape_input = st.toggle("Landscape", value=False)
    st.markdown("**Margins (mm)**")
    col1, col2 = st.columns(2)
    with col1:
        margin_top_input    = st.number_input("Top",    min_value=5, max_value=60, value=20)
        margin_left_input   = st.number_input("Left",   min_value=5, max_value=60, value=15)
    with col2:
        margin_bottom_input = st.number_input("Bottom", min_value=5, max_value=60, value=20)
        margin_right_input  = st.number_input("Right",  min_value=5, max_value=60, value=15)

    st.divider()
    st.header("Typography")
    st.caption("Set to 0 to inherit Notion's defaults.")
    body_font_input   = st.slider("Body font size (pt)",  0, 14, 0)
    table_font_input  = st.slider("Table font size (pt)", 0, 14, 0)
    line_height_input = st.slider("Line height", 0.0, 2.0, 0.0, step=0.1,
                                   format="%.1f")

    st.divider()
    st.header("Output")
    pdf_title_input  = st.text_input("PDF title (metadata)",  "")
    pdf_author_input = st.text_input("PDF author (metadata)", "")
    filename_input   = st.text_input("Output filename", "",
                                      placeholder="Leave blank to use document name")

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
            # Use a persistent temp dir so images stay accessible during render
            tmp_dir = tempfile.mkdtemp()
            output_path = os.path.join(tmp_dir, "notion_report.pdf")

            try:
                if uploaded_file.name.endswith('.zip'):
                    html_path = extract_zip(uploaded_file.getvalue(), tmp_dir)
                    if html_path is None:
                        st.error("No HTML file found inside the ZIP.")
                        st.stop()
                    default_name = os.path.basename(html_path).replace('.html', '')
                else:
                    html_path = os.path.join(tmp_dir, uploaded_file.name)
                    with open(html_path, 'wb') as f:
                        f.write(uploaded_file.getvalue())
                    default_name = uploaded_file.name.replace('.html', '')

                output_name = (filename_input.strip() or default_name) + ".pdf"

                asyncio.run(generate_pdf(
                    html_path, output_path,
                    header_left=header_left_input,
                    header_centre=header_centre_input,
                    header_right=header_right_input,
                    footer_left=footer_left_input,
                    footer_right=footer_right_input,
                    suppress_first_page_hf=suppress_hf_p1,
                    page_size=page_size_input,
                    landscape=landscape_input,
                    margin_top=margin_top_input,
                    margin_bottom=margin_bottom_input,
                    margin_left=margin_left_input,
                    margin_right=margin_right_input,
                    body_font_size=body_font_input,
                    table_font_size=table_font_input,
                    line_height=line_height_input,
                    pdf_title=pdf_title_input,
                    pdf_author=pdf_author_input,
                ))

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