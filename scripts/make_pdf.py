
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
import io

def make_simple_pdf(title:str, table_rows:list[list[str]], chart_png:bytes|None=None)->bytes:
    buf=io.BytesIO()
    doc=SimpleDocTemplate(buf, pagesize=A4, rightMargin=36,leftMargin=36, topMargin=36, bottomMargin=36)
    styles=getSampleStyleSheet()
    elems=[Paragraph(title, styles['Title']), Spacer(1,12)]
    if chart_png:
        img=Image(io.BytesIO(chart_png), width=400, height=300)
        elems.append(img); elems.append(Spacer(1,12))
    if table_rows:
        t=Table(table_rows)
        t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.5,colors.grey),
                               ('BACKGROUND',(0,0),(-1,0),colors.whitesmoke),
                               ('ALIGN',(0,0),(-1,-1),'LEFT')]))
        elems.append(t)
    doc.build(elems)
    return buf.getvalue()
