import pandas as pd
import sqlite3
import uuid

def import_docents_from_excel(excel_file_path):
    """
    Import docent information from Excel file into the database.
    
    Expected Excel columns:
    - Name: Full name of the docent
    - Email: Email address
    - Phone: Phone number
    - Neighborhood: Area of Pittsburgh they live in
    - CanDrive: Yes/No or True/False indicating if they can drive
    """
    # Read the Excel file
    print(f"Reading data from {excel_file_path}...")
    df = pd.read_excel(excel_file_path)
    
    # Connect to the database
    conn = sqlite3.connect('docent_rideshare.db')
    cursor = conn.cursor()
    
    # Make sure the docents table exists
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS docents (
        id TEXT PRIMARY KEY,
        name TEXT,
        email TEXT UNIQUE,
        phone TEXT,
        neighborhood TEXT,
        can_drive BOOLEAN
    )
    ''')
    
    # Create ride_offers and ride_requests tables if they don't exist
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ride_offers (
        id TEXT PRIMARY KEY,
        docent_id TEXT,
        date TEXT,
        time TEXT,
        from_location TEXT,
        to_location TEXT,
        seats_available INTEGER,
        is_tuesday_learning BOOLEAN,
        FOREIGN KEY (docent_id) REFERENCES docents (id)
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ride_requests (
        id TEXT PRIMARY KEY,
        docent_id TEXT,
        date TEXT,
        time TEXT,
        from_location TEXT,
        to_location TEXT,
        is_tuesday_learning BOOLEAN,
        matched_with TEXT,
        FOREIGN KEY (docent_id) REFERENCES docents (id)
    )
    ''')
    
    # Clean up column names (in case they have spaces or different capitalization)
    df.columns = [col.strip().lower() for col in df.columns]
    
    # Map expected columns to actual columns
    column_mapping = {
        'name': next((col for col in df.columns if 'first name' in col), None),
        'email': next((col for col in df.columns if 'email' in col), None),
        'phone': next((col for col in df.columns if 'phone' in col), None),
        'neighborhood': next((col for col in df.columns if 'neighborhood' in col or 'area' in col or 'location' in col), None),
        'can_drive': next((col for col in df.columns if 'drive' in col or 'car' in col), None)
    }
    
    # Validate required columns exist
    missing_columns = [name for name, col in column_mapping.items() if col is None]
    if missing_columns:
        print(f"Error: Missing required columns: {', '.join(missing_columns)}")
        print(f"Available columns: {', '.join(df.columns)}")
        conn.close()
        return
    
    # Process each row
    successful_imports = 0
    duplicates = 0
    errors = 0
    
    for index, row in df.iterrows():
        try:
            # Generate a unique ID for this docent
            docent_id = str(uuid.uuid4())
            
            # Extract values from row
            name = str(row[column_mapping['name']])
            email = str(row[column_mapping['email']]).lower().strip()
            phone = str(row[column_mapping['phone']])
            neighborhood = str(row[column_mapping['neighborhood']])
            
            # Handle different ways "can drive" might be represented
            can_drive_val = row[column_mapping['can_drive']]
            if isinstance(can_drive_val, bool):
                can_drive = can_drive_val
            elif isinstance(can_drive_val, (int, float)):
                can_drive = bool(can_drive_val)
            else:
                can_drive_str = str(can_drive_val).lower().strip()
                can_drive = can_drive_str in ['yes', 'true', '1', 'y', 't']
            
            # Insert into database, ignoring duplicates based on email
            try:
                cursor.execute(
                    'INSERT INTO docents (id, name, email, phone, neighborhood, can_drive) VALUES (?, ?, ?, ?, ?, ?)',
                    (docent_id, name, email, phone, neighborhood, can_drive)
                )
                successful_imports += 1
            except sqlite3.IntegrityError:
                # Email already exists
                duplicates += 1
                print(f"Skipping duplicate email: {email}")
                
        except Exception as e:
            errors += 1
            print(f"Error processing row {index}: {e}")
    
    # Commit changes
    conn.commit()
    conn.close()
    
    print(f"\nImport complete:")
    print(f"- {successful_imports} docents successfully imported")
    print(f"- {duplicates} duplicate emails skipped")
    print(f"- {errors} errors encountered")
    print("\nThe database is now ready to use with the email system.")

if __name__ == "__main__":
    excel_file = input("Enter the path to your Excel file: ")
    excel_file = '/Users/saranya/Desktop/testing.xlsx'
    import_docents_from_excel(excel_file)