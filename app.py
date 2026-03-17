import streamlit as st
import asyncio
import sys
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import tempfile
import os

# --- MANDATORY WINDOWS FIX ---
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
# --- Core Processing Logic ---
async def generate_pdf(input_html_path, output_pdf_path, header_text, footer_text):
    with open(input_html_path, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')

    notion_grey = "#91918e"
    
    # CSS Customisation: 
    # 1. No wrapping: white-space: nowrap
    # 2. Scaling: We use a print-media scale-to-fit approach
    custom_styles = f"""
    <style>
        @media print {{
            hr {{ break-before: page; visibility: hidden; height: 0; margin: 0 !important; }}
            
            /* Table Scaling Logic: No wrapping, scale to fit width */
            table {{ 
                width: 100% !important; 
                table-layout: auto !important; 
                white-space: nowrap !important; 
            }}
            
            /* Force tables to shrink if they exceed page width */
            .simple-table {{
                max-width: 100%;
                overflow: visible;
            }}

            body {{ 
                font-family: ui-sans-serif, -apple-system, sans-serif;
                color: rgb(55, 53, 47); 
            }}
            
            img, figure {{ break-inside: avoid; }}
        }}
    </style>
    """
    if soup.head:
        soup.head.append(BeautifulSoup(custom_styles, 'html.parser'))

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content(str(soup), wait_until="networkidle")
        
        # Injecting JS to scale tables that are too wide
        await page.evaluate('''() => {
            const tables = document.querySelectorAll('table');
            tables.forEach(table => {
                const pageWidth = 800; // Approx A4 width in pixels at standard DPI
                if (table.offsetWidth > pageWidth) {
                    const scale = pageWidth / table.offsetWidth;
                    table.style.transform = `scale(${scale})`;
                    table.style.transformOrigin = 'top left';
                }
            });
        }''')

        shared_style = f"font-family: ui-sans-serif, -apple-system, sans-serif; font-size: 10px; color: {notion_grey};"

        await page.pdf(
            path=output_pdf_path,
            format="A4",
            display_header_footer=True,
            header_template=f'<div style="{shared_style} width: 100%; text-align: center; margin: 0 40px;">{header_text}</div>',
            footer_template=f'''
                <div style="{shared_style} width: 100%; padding: 0 40px; display: flex; justify-content: space-between;">
                    <span>{footer_text}</span>
                    <span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>
                </div>''',
            margin={"top": "80px", "bottom": "80px", "left": "50px", "right": "50px"}
        )
        await browser.close()

# --- Streamlit WebUI ---
st.set_page_config(page_title="Notion Engineering PDF Tool", page_icon="📑")

st.title("📑 Notion to Engineering PDF")
st.caption("Custom exporter for University of Bath - EE22005")

with st.sidebar:
    st.header("Settings")
    header_input = st.text_input("Header Text", "")
    footer_input = st.text_input("Footer Text", "")

uploaded_file = st.file_uploader("Upload your Notion HTML export", type=['html'])

if uploaded_file is not None:
    if st.button("Generate PDF"):
        with st.spinner("Processing tables and rendering..."):
            # Create temp files for processing
            with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp_html:
                tmp_html.write(uploaded_file.getvalue())
                tmp_path = tmp_html.name
            
            output_path = "formatted_report.pdf"
            
            # Run the async PDF generation
            asyncio.run(generate_pdf(tmp_path, output_path, header_input, footer_input))
            
            # Provide download link
            with open(output_path, "rb") as f:
                st.success("PDF Generated Successfully!")
                st.download_button(
                    label="Download PDF",
                    data=f,
                    file_name=output_path,
                    mime="application/pdf"
                )
            
            # Cleanup
            os.remove(tmp_path)