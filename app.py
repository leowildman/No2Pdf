import streamlit as st
import asyncio
import sys
import os
import tempfile
import subprocess
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# --- MANDATORY WINDOWS FIX ---
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# --- Core Processing Logic ---
async def generate_pdf(input_html_path, output_pdf_path, header_text, footer_text):
    with open(input_html_path, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')

    notion_grey = "#91918e"
    
    # CSS Customisation for Notion Aesthetic
    custom_styles = f"""
    <style>
        @media print {{
            hr {{ break-before: page; visibility: hidden; height: 0; margin: 0 !important; }}
            
            /* Engineering Table Style: No wrapping, border sync */
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
        # Launch with no-sandbox flags for Cloud/Linux environments
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
        page = await browser.new_page()
        await page.set_content(str(soup), wait_until="networkidle")
        
        # JavaScript Scaling: Shrinks tables to fit A4 width (approx 720px with margins)
        await page.evaluate('''() => {
            const tables = document.querySelectorAll('table');
            const pageWidth = 720; 
            tables.forEach(table => {
                const currentWidth = table.offsetWidth;
                if (currentWidth > pageWidth) {
                    const scaleFactor = pageWidth / currentWidth;
                    table.style.transform = `scale(${scaleFactor})`;
                    table.style.transformOrigin = 'top left';
                    // Prevent large empty vertical spaces after scaling
                    table.parentElement.style.height = (table.offsetHeight * scaleFactor) + "px";
                    table.parentElement.style.overflow = "hidden";
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
st.info("Adaptive table scaling for University of Bath coursework.")

with st.sidebar:
    st.header("Report Configuration")
    header_input = st.text_input("Header Text", "EE22005: Engineering Practice and Design")
    footer_input = st.text_input("Footer Text", "")
    st.divider()
    st.caption("Tables are scaled proportionally to fit the page without text wrapping.")

uploaded_file = st.file_uploader("Upload Notion HTML", type=['html'])

if uploaded_file is not None:
    if st.button("Generate & Download PDF", type="primary"):
        # --- CLOUD BROWSER INSTALLATION CHECK ---
        if sys.platform != "win32":
            with st.spinner("Ensuring browser dependencies are installed..."):
                try:
                    subprocess.run(["playwright", "install", "chromium"], check=True)
                except Exception as e:
                    st.error(f"Browser install failed: {e}")

        with st.spinner("Rendering report..."):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp_html:
                tmp_html.write(uploaded_file.getvalue())
                tmp_path = tmp_html.name
            
            output_path = "notion_report_scaled.pdf"
            
            try:
                asyncio.run(generate_pdf(tmp_path, output_path, header_input, footer_input))
                
                with open(output_path, "rb") as f:
                    st.success("PDF Generation Complete!")
                    st.download_button(
                        label="Download PDF",
                        data=f,
                        file_name=f"{uploaded_file.name.replace('.html', '')}_scaled.pdf",
                        mime="application/pdf"
                    )
            except Exception as e:
                st.error(f"Processing Error: {e}")
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                if os.path.exists(output_path):
                    os.remove(output_path)