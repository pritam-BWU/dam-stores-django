Place PDF background images in this folder.

Required names:

- `statement_first_page.png` - first page background with header and watermark
- `statement_other_page.png` - next pages background with watermark only

`jpg` and `jpeg` files with the same base names are also supported if PNG is not used.

Manual positioning:

- Edit `OWNER_INFO_Y` in `Dam_stores/pdf_statement.py` to move the name/amount row.
- Edit `TABLE_TOP_Y` in `Dam_stores/pdf_statement.py` to move the first-page table.
- Edit `OTHER_PAGE_TABLE_TOP_Y` in `Dam_stores/pdf_statement.py` to move the table on page 2 and later.
- Larger Y value moves content upward.
- Smaller Y value moves content downward.
