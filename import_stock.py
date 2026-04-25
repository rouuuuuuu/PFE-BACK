import os
import django
import openpyxl

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'automation_project.settings')
django.setup()

from inventory.models import StockItem 

def safe_int(value):
    """Safely converts Excel cell values to integers."""
    if value is None:
        return 0
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return 0

def import_excel(file_path):
    if not os.path.exists(file_path):
        print(f"❌ Error: The file was not found at {file_path}")
        return

    print(f"Loading Excel file: {file_path} ...")
    
    # data_only=True reads the values, not the formulas
    wb = openpyxl.load_workbook(file_path, data_only=True)
    sheet = wb.active

    # Optional: Clear the existing table if you want a fresh start every time you run the script
    # StockItem.objects.all().delete()

    count = 0
    
    # min_row=3 skips the two header rows
    for row in sheet.iter_rows(min_row=3, values_only=True):
        ref = row[0]
        
        # Skip if the reference is empty
        if ref is None:
            continue
            
        ref_str = str(ref).strip()
        name_str = str(row[1]).strip() if row[1] else "N/A"
        class_str = str(row[2]).strip() if row[2] else "N/A"
        vendor_str = str(row[3]).strip() if row[3] else ""

        # Option A: Get the existing item or create a new one with 0 values
        item, created = StockItem.objects.get_or_create(
            reference=ref_str,
            defaults={
                'name': name_str,
                'classification': class_str,
                'vendor': vendor_str,
                'stock_qte': 0,
                'transfert_nbr_u': 0,
                'transfert_qte': 0
            }
        )

        # Update the item by ADDING the new quantities (Option A logic)
        item.stock_qte += safe_int(row[10])
        item.transfert_nbr_u += safe_int(row[15]) if len(row) > 15 else 0
        item.transfert_qte += safe_int(row[16]) if len(row) > 16 else 0
        
        # Save the updated totals to the DB
        item.save()
        count += 1
        
    print(f"✅ Finished! Processed {count} rows into {StockItem.objects.count()} unique items.")

if __name__ == '__main__':
    import_excel('/home/roua/bd/Etat_stock_12032026.xlsx')
