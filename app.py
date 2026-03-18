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
    # Safety clamp — prevents bad margin values from causing layout clipping
    margin_top    = max(5, int(margin_top))
    margin_bottom = max(5, int(margin_bottom))
    margin_left   = max(5, int(margin_left))
    margin_right  = max(5, int(margin_right))

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

    # Only inject typography overrides if user moved sliders off 0 (0 = Notion default)
    body_font_css   = f"font-size: {body_font_size}pt !important;"  if body_font_size  > 0   else ""
    table_font_css  = f"font-size: {table_font_size}pt !important;" if table_font_size  > 0   else ""
    line_height_css = f"line-height: {line_height} !important;"     if line_height      > 0.0 else ""

    custom_styles = f"""
    <style>
        /* Noto Color Emoji is loaded as a web font so emoji render correctly
           on Linux servers where the system emoji font is not installed */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Noto+Color+Emoji&display=swap');

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
                /* Inter loads from Google Fonts; Liberation/DejaVu are the Linux
                   system font fallbacks installed via packages.txt */
                font-family: 'Inter', 'Liberation Sans', 'DejaVu Sans', Arial, sans-serif !important;
                color: rgb(55, 53, 47);
                {body_font_css}
                {line_height_css}
            }}
            img, figure {{ break-inside: avoid; max-width: 100% !important; }}
            /* Notion's cover image uses max-height:30vh which behaves differently
               in Playwright's print context vs screen, clipping the top of the image.
               Fix it to a sensible fixed height so it renders fully. */
            .page-cover-image {{
                max-height: 250px !important;
                width: 100% !important;
                object-fit: cover !important;
                object-position: center !important;
                display: block !important;
            }}
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
            margin_top_val    = '0'     if i == 0                    else '0.6em'
            margin_bottom_val = '0'     if i == len(paragraphs) - 1  else '0.6em'
            p_tag['style'] = f"{existing}; margin-top:{margin_top_val}; margin-bottom:{margin_bottom_val};"

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
        # Compute printable height in px from the chosen page size and margins.
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

        # Header/footer: three-column layout matching doc 6's 40px side padding
        shared_style = f"font-family: ui-sans-serif, -apple-system, sans-serif; font-size: 10px; color: {notion_grey};"

        def three_col(left, centre, right):
            return (
                f'<div style="{shared_style} width:100%; padding:0 40px; '
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
        # a white fixed block — only affects the first page as subsequent
        # pages scroll past it.
        if suppress_first_page_hf:
            await page.evaluate(f'''() => {{
                const s = document.createElement('style');
                s.textContent = `@media print {{
                    .hf-suppress-top    {{ position:fixed; top:0;    left:0; right:0; height:{margin_top}mm;    background:white; z-index:99999; }}
                    .hf-suppress-bottom {{ position:fixed; bottom:0; left:0; right:0; height:{margin_bottom}mm; background:white; z-index:99999; }}
                }}`;
                document.head.appendChild(s);
                const t = document.createElement('div'); t.className = 'hf-suppress-top';
                const b = document.createElement('div'); b.className = 'hf-suppress-bottom';
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
st.set_page_config(page_title="Notion to PDF", page_icon="📑")

st.title("📑 Notion to PDF")
st.caption("Convert any Notion HTML export to a clean, print-ready PDF.")

MARGIN_PRESETS = {
    "Normal": (21, 21, 11, 11),
    "Narrow": (13, 13, 13, 13),
    "Wide":   (25, 25, 25, 25),
}

# Initialise session state defaults on first run
_defaults = dict(
    hl='', hc='', hr='',
    fl='', fr='',
    suppress_p1=False,
    page_size_sel='A4',
    landscape=False,
    margin_top=25, margin_bottom=25,
    margin_left=11, margin_right=11,
    body_font=0, table_font=0, line_height=0.0,
    pdf_title='', pdf_author='', filename='',
)
for _k, _v in _defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

with st.sidebar:
    st.header("Header")
    header_left_input   = st.text_input("Left",   key="hl")
    header_centre_input = st.text_input("Centre", key="hc")
    header_right_input  = st.text_input("Right",  key="hr")

    st.header("Footer")
    footer_left_input  = st.text_input("Left",                              key="fl")
    footer_right_input = st.text_input("Right (page number auto-appended)", key="fr")
    suppress_hf_p1     = st.toggle("Suppress on page 1", key="suppress_p1",
                                    help="Hides header & footer on the cover page.")

    st.divider()
    with st.expander("📐 Layout", expanded=False):
        page_size_input = st.selectbox("Page size", ["A4", "Letter", "A3"],
                                        index=["A4", "Letter", "A3"].index(st.session_state.get('page_size_sel', 'A4')),
                                        key="page_size_sel")
        landscape_input = st.toggle("Landscape", key="landscape")

        st.markdown("**Margin preset**")
        _pc = st.columns(3)
        for _i, (_name, _vals) in enumerate(MARGIN_PRESETS.items()):
            with _pc[_i]:
                if st.button(_name, use_container_width=True):
                    st.session_state.margin_top    = _vals[0]
                    st.session_state.margin_bottom = _vals[1]
                    st.session_state.margin_left   = _vals[2]
                    st.session_state.margin_right  = _vals[3]
                    st.rerun()

        st.markdown("**Margins (mm)**")
        _c1, _c2 = st.columns(2)
        with _c1:
            margin_top_input    = st.number_input("Top",    min_value=5, max_value=60, key="margin_top")
            margin_left_input   = st.number_input("Left",   min_value=5, max_value=60, key="margin_left")
        with _c2:
            margin_bottom_input = st.number_input("Bottom", min_value=5, max_value=60, key="margin_bottom")
            margin_right_input  = st.number_input("Right",  min_value=5, max_value=60, key="margin_right")

    with st.expander("🔤 Typography", expanded=False):
        st.caption("Leave at 0 to use Notion's defaults.")
        body_font_input   = st.slider("Body font size (pt)",  0, 14, key="body_font")
        table_font_input  = st.slider("Table font size (pt)", 0, 14, key="table_font")
        line_height_input = st.slider("Line height", 0.0, 2.0, step=0.1, format="%.1f", key="line_height")

    with st.expander("📁 Output", expanded=False):
        pdf_title_input  = st.text_input("PDF title (metadata)",  key="pdf_title")
        pdf_author_input = st.text_input("PDF author (metadata)", key="pdf_author")
        filename_input   = st.text_input("Output filename", key="filename", placeholder="Leave blank to use document name")

    st.divider()
    if st.button("↺ Reset to defaults", use_container_width=True):
        for _k, _v in _defaults.items():
            st.session_state[_k] = _v
        st.rerun()

with st.expander("📖 How to export from Notion", expanded=False):
    st.markdown("**Step 1 — Open your Notion page**")
    st.write("Navigate to the page you want to export in Notion.")
    st.divider()
    st.markdown("**Step 2 — Open the export menu**")
    st.write("Click the **⋯** menu in the top-right corner of the page, then select **Export**.")
    st.divider()
    st.markdown("**Step 3 — Set export options**")
    st.write("Set the export format to **HTML** and make sure **Include subpages** and "
             "**Create folders for subpages** are set as needed. Then click **Export**.")
    st.divider()
    st.markdown("**Step 4 — Upload the ZIP below**")
    st.write("Notion will download a `.zip` file. Upload it directly below — no need to unzip it.")

uploaded_file = st.file_uploader("Upload Notion HTML or ZIP", type=['html', 'zip'])

if uploaded_file is not None:
    if st.button("Generate PDF", type="primary", use_container_width=True):
        tmp_dir     = tempfile.mkdtemp()
        output_path = os.path.join(tmp_dir, "notion_report.pdf")
        pdf_bytes   = None
        output_name = "output.pdf"

        with st.status("Generating PDF…", expanded=True) as status:
            try:
                st.write("🔧 Preparing browser engine…")
                if sys.platform != "win32":
                    subprocess.run(
                        [sys.executable, "-m", "playwright", "install", "chromium"],
                        check=True, capture_output=True,
                    )

                st.write("📦 Processing document…")
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

                st.write("🖨️ Rendering PDF…")
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

                pdf_bytes = open(output_path, 'rb').read()
                status.update(label="✅ PDF ready!", state="complete", expanded=False)

            except Exception as e:
                status.update(label="❌ Error", state="error", expanded=True)
                st.error(f"Processing error: {e}")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        if pdf_bytes:
            file_size = len(pdf_bytes)
            size_str  = f"{file_size/1024:.0f} KB" if file_size < 1024*1024 else f"{file_size/1024/1024:.1f} MB"
            st.caption(f"📄 {output_name}  ·  {size_str}")
            st.download_button(
                label="⬇️ Download PDF",
                data=pdf_bytes,
                file_name=output_name,
                mime="application/pdf",
                use_container_width=True,
            )
            st.toast("PDF generated successfully!", icon="✅")