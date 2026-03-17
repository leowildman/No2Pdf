import streamlit as st
import asyncio
import sys
import os
import tempfile
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# --- MANDATORY WINDOWS FIX ---
# This prevents the NotImplementedError on Windows machines
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# --- Core Processing Logic ---
async def generate_pdf(input_html_path, output_pdf_path, header_text, footer_text):
    with open(input_html_path, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')

    notion_grey = "#91918e"
    
    # CSS Customisation: 
    # 1. Page breaks at horizontal rules
    # 2. Notion-style typography
    # 3. Table scaling preparation
    custom_styles = f"""
    <style>
        @media print {{
            hr {{ break-before: page; visibility: hidden; height: 0; margin: 0 !important; }}
            
            table {{ 
                width: 100% !important; 
                table-layout: auto !important; 
                white-space: nowrap !important; 
                border-collapse: collapse !important;
            }}

            td, th {{
                border: 1px solid rgba(55, 53, 47, 0.09) !important;
                padding: 6px !important;
                font-size: 9pt !important;
            }}

            body {{ 
                font-family: ui-sans-serif, -apple-system, system-ui, "Segoe UI", Helvetica, Arial, sans-serif;
                color: rgb(55, 53, 47); 
            }}
            
            img, figure {{ break-inside: avoid; }}
        }}
    </style>
    """
    if soup.head:
        soup.head.append(BeautifulSoup(custom_styles, 'html.parser'))

    async with async_playwright() as p:
        # Added --no-sandbox flags for Streamlit Cloud (Linux) compatibility
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
        page = await browser.new_page()
        await page.set_content(str(soup), wait_until="networkidle")
        
        # JavaScript Injection: Scales tables down ONLY if they exceed the page width
        await page.evaluate('''() => {
            const tables = document.querySelectorAll('table');
            const pageWidth = 720; // Standard A4 width in pixels at 96 DPI minus margins
            tables.forEach(table => {
                const currentWidth = table.offsetWidth;
                if (currentWidth > pageWidth) {
                    const scaleFactor = pageWidth / currentWidth;
                    table.style.transform = `scale(${scaleFactor})`;
                    table.style.transformOrigin = 'top left';
                    // Adjust container height to prevent large gaps after scaled tables
                    table.parentElement.style.height = (table.offsetHeight * scaleFactor) + "px";
                }
            });
        }''')

        shared_style = f"font-family: ui-sans-serif, -apple-system, sans-serif; font-size: 10px; color: {notion_grey};"

        await page.pdf(
            path=output_pdf_path,
            format="A4",
            display_header_footer=True,
            header_template=f'<div style="{shared_style} width: 100%; text-align: center; margin: 0 50px;">{header_text}</div>',
            footer_template=f'''
                <div style="{shared_style} width: 100%; padding: 0 50px; display: flex; justify-content: space-between;">
                    <span>{footer_text}</span>
                    <span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>
                </div>''',
            margin={"top": "80px", "bottom": "80px", "left": "50px", "right": "50px"}
        )
        await browser.close()

# --- Streamlit WebUI ---
st.set_page_config(page_title="Notion Engineering PDF Tool", page_icon="📑")

st.title("📑 Notion to Engineering PDF")
st.info("Upload your Notion HTML export to generate a scaled, paginated PDF with custom headers.")

with st.sidebar:
    st.header("Report Details")
    header_input = st.text_input("Header Text", "EE22005: Engineering Practice and Design")
    footer_input = st.text_input("Footer Text", "")
    st.divider()
    st.caption("This tool scales wide tables automatically to fit A4 width without wrapping text.")

uploaded_file = st.file_uploader("Choose Notion HTML file", type=['html'])

if uploaded_file is not None:
    if st.button("Generate & Download PDF", type="primary"):
        with st.spinner("Scaling tables and rendering PDF..."):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp_html:
                tmp_html.write(uploaded_file.getvalue())
                tmp_path = tmp_html.name
            
            output_path = "formatted_report.pdf"
            
            try:
                asyncio.run(generate_pdf(tmp_path, output_path, header_input, footer_input))
                
                with open(output_path, "rb") as f:
                    st.success("PDF Ready!")
                    st.download_button(
                        label="Click to Download",
                        data=f,
                        file_name=f"{uploaded_file.name.replace('.html', '')}.pdf",
                        mime="application/pdf"
                    )
            except Exception as e:
                st.error(f"An error occurred: {e}")
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)