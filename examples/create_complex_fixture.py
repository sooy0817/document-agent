"""Author a fictional two-page PDF; expected layout never enters the parser.

Optional authoring dependency: reportlab. The runtime demo needs only the PDF.
"""
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

ROOT = Path(__file__).resolve().parent


def main():
    c = canvas.Canvas(str(ROOT / 'complex-synthetic.pdf'), pagesize=(612, 792), invariant=1)
    c.setTitle('Document Agent | Complex table fixture (fictional)')
    c.setAuthor('Document Agent')

    def text(x, y, value, size=10, bold=False, color='#183044'):
        c.setFillColor(HexColor(color))
        c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
        c.drawString(x, 792-y, value)

    def line(x0, y0, x1, y1):
        c.setStrokeColor(HexColor('#314A5B')); c.setLineWidth(.65)
        c.line(x0, 792-y0, x1, 792-y1)

    def fill(x, y, w, h, color):
        c.setFillColor(HexColor(color)); c.rect(x, 792-y-h, w, h, fill=1, stroke=0)

    def heading(n, title, subtitle):
        text(42, 42, 'DOCUMENT AGENT  /  SYNTHETIC SOURCE', 9, True, '#287E7B')
        text(42, 81, title, 23, True)
        text(42, 106, subtitle, 10)
        text(42, 752, 'Fictional data. Parser output is a candidate, not an approved financial fact.', 9)
        text(552, 752, str(n), 9)

    heading(1, 'Merged headers. Two axes.', 'A compact fixture for column groups, row groups, blank, zero and negative values.')
    text(42, 147, '01  /  CONSOLIDATED POSITION', 11, True)
    text(42, 170, 'Amounts: fictional units (FU). Shares: %. Periods: year-end 2024 and 2025.', 9)
    xs = [42, 120, 270, 345, 420, 495, 570]
    ys = [193, 229, 265, 311, 357, 403, 449]
    fill(42, 193, 528, 72, '#E8F0F4')
    fill(42, 265, 78, 184, '#F1F6F6')
    for y in [193, 265, 357, 449]: line(42, y, 570, y)
    line(270, 229, 570, 229)
    for y in [311, 403]: line(120, y, 570, y)
    for x in [42, 120, 270, 420, 570]: line(x, 193, x, 449)
    for x in [345, 495]: line(x, 229, x, 449)
    text(51, 233, 'Group', 10, True); text(133, 233, 'Item', 10, True)
    text(324, 216, '2024', 11, True); text(474, 216, '2025', 11, True)
    for x, label in [(279,'Amount'),(354,'Share'),(429,'Amount'),(504,'Share')]: text(x, 252, label, 10, True)
    text(51, 316, 'Assets', 10, True); text(51, 408, 'Liabilities', 10, True)
    rows = [('Cash', '120', '30', '150', '33.3'), ('Receivables', '280', '70', '300', '66.7'),
            ('Short-term debt', '80', '', '0', ''), ('Adjustment', '-10', '', '-12', '')]
    for i, row in enumerate(rows):
        for x, value in zip([132, 280, 355, 430, 505], row): text(x, 293+46*i, value, 10)
    text(42, 483, 'Notes on this fixture', 12, True)
    for y, s in [(507,'Assets and Liabilities are vertically merged row labels.'),
                 (528,'Each year spans Amount and Share columns. Group and Item span both header rows.'),
                 (549,'Blank shares are intentionally blank; the 2025 debt value is explicitly zero.'),
                 (570,'Adjustment values retain their original minus sign.')]: text(42,y,s,10)
    c.showPage()

    heading(2, 'A table inside a cell.', 'The parent cell contains a paragraph, an independent grid and a following note.')
    text(42, 147, '02  /  PRODUCT INFORMATION', 11, True)
    fill(42, 174, 528, 38, '#E8F0F4')
    for y in [174, 212, 650, 700]: line(42,y,570,y)
    for x in [42,154,570]: line(x,174,x,700)
    text(54,199,'Category',11,True); text(166,199,'Details',11,True)
    text(54,250,'Housing',11,True)
    text(54,268,'support',11,True)
    text(171,251,'Eligibility is reviewed at renewal. The limits below apply',10)
    text(171,270,'to each household category. All values are fictional.',10)
    text(171,312,'Annual limits by household',11,True)
    fill(177,340,369,34,'#E8F0F4')
    for y in [340,374,416,458,500]: line(177,y,546,y)
    for x in [177,352,449,546]: line(x,340,x,500)
    for x,v in [(189,'Household'),(365,'2024'),(462,'2025')]: text(x,362,v,10,True)
    for i,row in enumerate([('One person','40','45'),('Two people','65','70'),('Three or more','80','90')]):
        for x,v in zip([189,365,462],row): text(x,400+42*i,v,10)
    text(171,543,'Unit: fictional units per year. These are illustrative limits.',10)
    text(171,564,'A renewal requires a fresh review; approval is not automatic.',10)
    text(54,679,'Contact',10,True); text(166,679,'Use the fictional service portal for application details.',10)
    c.save()
    print(ROOT / 'complex-synthetic.pdf')


if __name__ == '__main__':
    main()
