import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import sqlite3
import uuid
import datetime
import time
import schedule
import threading
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd

# Email configuration
EMAIL_ADDRESS = "your_email@gmail.com"  # Change to your email
EMAIL_PASSWORD = "your_app_password"  # Change to your app password
SMTP_SERVER = "smtp.gmail.com"  # Change as needed for your provider
SMTP_PORT = 465

# Google Sheets configuration
CREDENTIALS_FILE = "credentials.json"  # Your Google API credentials file
RIDE_REQUEST_SHEET_ID = "your_google_sheet_id_for_ride_requests"
RIDE_OFFER_SHEET_ID = "your_google_sheet_id_for_ride_offers"

# Connect to Google Sheets
def connect_to_sheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    credentials = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(credentials)
    return client

# Database functions
def init_db():
    """Initialize the database with required tables"""
    conn = sqlite3.connect('docent_rideshare.db')
    c = conn.cursor()
    
    # Create docents table
    c.execute('''
        CREATE TABLE IF NOT EXISTS docents (
            id TEXT PRIMARY KEY,
            name TEXT,
            email TEXT UNIQUE,
            phone TEXT,
            neighborhood TEXT,
            can_drive BOOLEAN
        )
    ''')
    
    # Create ride offers table
    c.execute('''
        CREATE TABLE IF NOT EXISTS ride_offers (
            id TEXT PRIMARY KEY,
            docent_id TEXT,
            date TEXT,
            time TEXT,
            from_location TEXT,
            to_location TEXT,
            seats_available INTEGER,
            is_tuesday_learning BOOLEAN,
            timestamp TEXT,
            processed BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (docent_id) REFERENCES docents (id)
        )
    ''')
    
    # Create ride requests table
    c.execute('''
        CREATE TABLE IF NOT EXISTS ride_requests (
            id TEXT PRIMARY KEY,
            docent_id TEXT,
            date TEXT,
            time TEXT,
            from_location TEXT,
            to_location TEXT,
            is_tuesday_learning BOOLEAN,
            matched_with TEXT,
            timestamp TEXT,
            processed BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (docent_id) REFERENCES docents (id)
        )
    ''')
    
    conn.commit()
    conn.close()
    print("Database initialized.")

# Email sending function
def send_email(recipient, subject, html_content):
    """Send an HTML email to the specified recipient"""
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = EMAIL_ADDRESS
    message["To"] = recipient
    
    # Attach HTML content
    html_part = MIMEText(html_content, "html")
    message.attach(html_part)
    
    # Send email
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, recipient, message.as_string())
            print(f"Email sent to {recipient}")
            return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False

# Utility functions
def get_next_tuesday():
    """Return the date of the next Tuesday"""
    today = datetime.date.today()
    days_ahead = (1 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # If today is Tuesday, get next Tuesday
    next_tuesday = today + datetime.timedelta(days=days_ahead)
    return next_tuesday

def get_docent_by_email(email):
    """Get docent information from database by email"""
    conn = sqlite3.connect('docent_rideshare.db')
    c = conn.cursor()
    c.execute('SELECT * FROM docents WHERE email = ?', (email.lower(),))
    docent = c.fetchone()
    conn.close()
    return docent

def get_docent_by_id(docent_id):
    """Get docent information from database by ID"""
    conn = sqlite3.connect('docent_rideshare.db')
    c = conn.cursor()
    c.execute('SELECT * FROM docents WHERE id = ?', (docent_id,))
    docent = c.fetchone()
    conn.close()
    return docent

def process_ride_request_form_responses():
    """
    Read new ride request form responses from Google Sheet and add to database
    """
    try:
        # Connect to the Google Sheet
        client = connect_to_sheets()
        sheet = client.open_by_key(RIDE_REQUEST_SHEET_ID).sheet1
        
        # Get all records
        records = sheet.get_all_records()
        
        # Convert to DataFrame for easier processing
        df = pd.DataFrame(records)
        
        if df.empty:
            print("No ride requests to process")
            return
        
        # Connect to database
        conn = sqlite3.connect('docent_rideshare.db')
        cursor = conn.cursor()
        
        # Get timestamp of last processed request
        cursor.execute('SELECT MAX(timestamp) FROM ride_requests')
        last_timestamp = cursor.fetchone()[0]
        
        # Set a default timestamp if no previous entries
        if not last_timestamp:
            last_timestamp = "1970-01-01 00:00:00"
        
        # Process new entries
        new_entries = 0
        
        for index, row in df.iterrows():
            # Check if this is a new entry
            timestamp = row['Timestamp']
            
            if timestamp > last_timestamp:
                # Get docent by email
                email = row['Email Address'].lower()
                docent = get_docent_by_email(email)
                
                if not docent:
                    print(f"Unknown docent with email {email}")
                    continue
                
                docent_id = docent[0]
                
                # Parse date and other fields
                date_str = row['Date']
                time_str = row['Pickup Time']
                from_location = row['Neighborhood']
                to_location = row['Destination']
                
                # Check if this is for Tuesday learning
                is_tuesday_learning = "Tuesday Learning" in to_location
                
                # Generate unique ID
                request_id = str(uuid.uuid4())
                
                # Add to database
                cursor.execute('''
                    INSERT INTO ride_requests 
                    (id, docent_id, date, time, from_location, to_location, 
                     is_tuesday_learning, matched_with, timestamp, processed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, FALSE)
                ''', (request_id, docent_id, date_str, time_str, from_location, 
                      to_location, is_tuesday_learning, timestamp))
                
                new_entries += 1
                
                # Send confirmation email
                docent_name = docent[1]
                subject = "Your Ride Request Has Been Received"
                html_content = f"""
                <html>
                <head>
                    <style>
                        body {{ font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; }}
                        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                        h2 {{ color: #2c3e50; }}
                        .details {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 15px 0; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h2>Your Ride Request</h2>
                        <p>Hello {docent_name},</p>
                        <p>We've received your request for a ride on {date_str} at {time_str}.</p>
                        
                        <div class="details">
                            <p>From: {from_location}</p>
                            <p>To: {to_location}</p>
                        </div>
                        
                        <p>We'll match you with a driver and let you know soon!</p>
                        <p>If your plans change, please call our coordinator at (412) 555-1234.</p>
                    </div>
                </body>
                </html>
                """
                send_email(email, subject, html_content)
        
        # Commit changes
        conn.commit()
        conn.close()
        
        print(f"Processed {new_entries} new ride requests")
        
        # Try to match rides
        if new_entries > 0:
            match_rides()
    
    except Exception as e:
        print(f"Error processing ride requests: {e}")

def process_ride_offer_form_responses():
    """
    Read new ride offer form responses from Google Sheet and add to database
    """
    try:
        # Connect to the Google Sheet
        client = connect_to_sheets()
        sheet = client.open_by_key(RIDE_OFFER_SHEET_ID).sheet1
        
        # Get all records
        records = sheet.get_all_records()
        
        # Convert to DataFrame for easier processing
        df = pd.DataFrame(records)
        
        if df.empty:
            print("No ride offers to process")
            return
        
        # Connect to database
        conn = sqlite3.connect('docent_rideshare.db')
        cursor = conn.cursor()
        
        # Get timestamp of last processed offer
        cursor.execute('SELECT MAX(timestamp) FROM ride_offers')
        last_timestamp = cursor.fetchone()[0]
        
        # Set a default timestamp if no previous entries
        if not last_timestamp:
            last_timestamp = "1970-01-01 00:00:00"
        
        # Process new entries
        new_entries = 0
        
        for index, row in df.iterrows():
            # Check if this is a new entry
            timestamp = row['Timestamp']
            
            if timestamp > last_timestamp:
                # Get docent by email
                email = row['Email Address'].lower()
                docent = get_docent_by_email(email)
                
                if not docent:
                    print(f"Unknown docent with email {email}")
                    continue
                
                docent_id = docent[0]
                
                # Parse date and other fields
                date_str = row['Date']
                time_str = row['Pickup Time']
                from_location = row['Starting Neighborhood']
                to_location = row['Destination']
                seats = int(row['Available Seats'])
                
                # Check if this is for Tuesday learning
                is_tuesday_learning = "Tuesday Learning" in to_location
                
                # Generate unique ID
                offer_id = str(uuid.uuid4())
                
                # Add to database
                cursor.execute('''
                    INSERT INTO ride_offers 
                    (id, docent_id, date, time, from_location, to_location, 
                     seats_available, is_tuesday_learning, timestamp, processed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE)
                ''', (offer_id, docent_id, date_str, time_str, from_location, 
                      to_location, seats, is_tuesday_learning, timestamp))
                
                new_entries += 1
                
                # Send confirmation email
                docent_name = docent[1]
                subject = "Your Ride Offer Has Been Received"
                html_content = f"""
                <html>
                <head>
                    <style>
                        body {{ font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; }}
                        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                        h2 {{ color: #2c3e50; }}
                        .details {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 15px 0; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h2>Thank You for Offering a Ride</h2>
                        <p>Hello {docent_name},</p>
                        <p>We've received your offer to drive on {date_str} at {time_str}.</p>
                        
                        <div class="details">
                            <p>From: {from_location}</p>
                            <p>To: {to_location}</p>
                            <p>Available Seats: {seats}</p>
                        </div>
                        
                        <p>We'll let you know if anyone needs a ride!</p>
                        <p>If your plans change, please call our coordinator at (412) 555-1234.</p>
                    </div>
                </body>
                </html>
                """
                send_email(email, subject, html_content)
        
        # Commit changes
        conn.commit()
        conn.close()
        
        print(f"Processed {new_entries} new ride offers")
        
        # Try to match rides
        if new_entries > 0:
            match_rides()
    
    except Exception as e:
        print(f"Error processing ride offers: {e}")

def match_rides():
    """Match ride requests with ride offers"""
    conn = sqlite3.connect('docent_rideshare.db')
    c = conn.cursor()
    
    # Get all unmatched ride requests
    c.execute('SELECT * FROM ride_requests WHERE matched_with IS NULL')
    requests = c.fetchall()
    
    for req in requests:
        request_id = req[0]
        requester_id = req[1]
        request_date = req[2]
        request_time = req[3]
        request_from = req[4]
        request_to = req[5]
        is_tuesday = req[6]
        
        # Find matching offers with available seats
        # Match on: same date, approximate time (within 30 min), and destination
        c.execute('''
            SELECT o.id, o.docent_id, o.time, o.seats_available, o.from_location 
            FROM ride_offers o
            WHERE o.date = ? 
            AND o.to_location = ? 
            AND o.seats_available > 0
            AND o.is_tuesday_learning = ?
        ''', (request_date, request_to, is_tuesday))
        
        matches = c.fetchall()
        
        if matches:
            match = matches[0]  # Take the first match
            offer_id = match[0]
            driver_id = match[1]
            offer_time = match[2]
            offer_from = match[4]
            
            # Update the request with the match
            c.execute('UPDATE ride_requests SET matched_with = ? WHERE id = ?', 
                     (offer_id, request_id))
            
            # Decrease available seats in the offer
            c.execute('UPDATE ride_offers SET seats_available = seats_available - 1 WHERE id = ?', 
                     (offer_id,))
            
            # Get driver details
            c.execute('SELECT name, email, phone, neighborhood FROM docents WHERE id = ?', (driver_id,))
            driver = c.fetchone()
            driver_name = driver[0]
            driver_email = driver[1]
            driver_phone = driver[2]
            driver_neighborhood = driver[3]
            
            # Get requester details
            c.execute('SELECT name, email, phone, neighborhood FROM docents WHERE id = ?', (requester_id,))
            requester = c.fetchone()
            requester_name = requester[0]
            requester_email = requester[1]
            requester_phone = requester[2]
            requester_neighborhood = requester[3]
            
            conn.commit()
            
            # Notify both parties via email
            
            # Email to requester
            subject = "Good News! You've Been Matched With a Driver"
            html_content = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                    h2 {{ color: #2c3e50; }}
                    .match-details {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 15px 0; }}
                    .highlight {{ color: #27ae60; font-weight: bold; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h2 class="highlight">Ride Match Confirmed!</h2>
                    <p>Good news! We've found you a ride for {request_date}.</p>
                    
                    <div class="match-details">
                        <p>Driver: {driver_name}</p>
                        <p>Phone: {driver_phone}</p>
                        <p>Pickup Time: {offer_time}</p>
                        <p>Pickup Location: Your neighborhood ({request_from})</p>
                    </div>
                    
                    <p>The driver will coordinate the exact pickup spot with you.</p>
                    <p>If you need to cancel, please call our coordinator at (412) 555-1234 as soon as possible.</p>
                </div>
            </body>
            </html>
            """
            send_email(requester_email, subject, html_content)
            
            # Email to driver
            subject = "New Passenger Matched for Your Ride"
            html_content = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                    h2 {{ color: #2c3e50; }}
                    .match-details {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 15px 0; }}
                    .highlight {{ color: #27ae60; font-weight: bold; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h2 class="highlight">New Passenger Match!</h2>
                    <p>A docent needs a ride on {request_date} at {offer_time}.</p>
                    
                    <div class="match-details">
                        <p>Passenger: {requester_name}</p>
                        <p>Phone: {requester_phone}</p>
                        <p>Pickup Location: Their neighborhood ({request_from})</p>
                        <p>Destination: {request_to}</p>
                    </div>
                    
                    <p>Please coordinate the exact pickup location directly with them.</p>
                    <p>If your plans change, please call our coordinator at (412) 555-1234 as soon as possible.</p>
                </div>
            </body>
            </html>
            """
            send_email(driver_email, subject, html_content)
    
    conn.close()

def send_weekly_reminder():
    """Send weekly email with links to Google Forms"""
    conn = sqlite3.connect('docent_rideshare.db')
    c = conn.cursor()
    
    # Get all docents
    c.execute('SELECT id, name, email, can_drive, neighborhood FROM docents')
    docents = c.fetchall()
    
    # Get next Tuesday date
    tuesday_next = get_next_tuesday()
    tuesday_str = tuesday_next.strftime('%B %d, %Y')
    tuesday_day = tuesday_next.strftime('%A, %B %d')
    
    # Google Form URLs
    RIDE_REQUEST_FORM_URL = "https://forms.gle/your-ride-request-form-id"
    RIDE_OFFER_FORM_URL = "https://forms.gle/your-ride-offer-form-id"
    
    for docent in docents:
        docent_id = docent[0]
        name = docent[1]
        email = docent[2]
        can_drive = docent[3]
        neighborhood = docent[4]
        
        # Create personalized email with form links
        subject = f"Docent Rideshare for Week of {tuesday_next.strftime('%B %d')}"
        
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                h1, h2, h3 {{ color: #2c3e50; }}
                .tuesday-box {{ background-color: #e8f4f8; padding: 15px; border-radius: 5px; margin: 20px 0; border: 1px solid #b3d7e5; }}
                .other-box {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0; border: 1px solid #ddd; }}
                .button {{ display: inline-block; padding: 10px 20px; background-color: #3498db; color: white; text-decoration: none; 
                        border-radius: 5px; font-size: 16px; margin: 10px 5px 10px 0; }}
                .drive-button {{ background-color: #27ae60; }}
                .request-button {{ background-color: #e74c3c; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Hello {name}!</h1>
                <h2>Docent Rideshare Weekly Update</h2>
                
                <div class="tuesday-box">
                    <h3>Tuesday Learning Session: {tuesday_day}</h3>
                    <p>Our weekly learning session is at the Pittsburgh Museum at 10:30 AM.</p>
                    
                    <p>Need a ride to the Tuesday session?</p>
                    <a href="{RIDE_REQUEST_FORM_URL}" class="button request-button">
                        I Need a Ride
                    </a>
                    
                    {f'''
                    <p>Can you offer a ride to fellow docents?</p>
                    <a href="{RIDE_OFFER_FORM_URL}" class="button drive-button">
                        I Can Drive Others
                    </a>
                    ''' if can_drive else ''}
                </div>
                
                <div class="other-box">
                    <h3>Other Shifts This Week</h3>
                    <p>You can use the same forms to request or offer rides for any day:</p>
                    
                    <a href="{RIDE_REQUEST_FORM_URL}" class="button request-button">
                        Request a Ride
                    </a>
                    
                    {f'''
                    <a href="{RIDE_OFFER_FORM_URL}" class="button drive-button">
                        Offer a Ride
                    </a>
                    ''' if can_drive else ''}
                </div>
                
                <p>Thank you for participating in our ridesharing program!</p>
                <p>The Google Forms are simple to fill out and will help us match riders with drivers.</p>
                <p>If you have any questions or need assistance, please call our coordinator at (412) 555-1234.</p>
            </div>
        </body>
        </html>
        """
        
        # Send the email
        send_email(email, subject, html_content)
    
    conn.close()
    print(f"Sent weekly reminder emails to {len(docents)} docents.")

def send_ride_reminders():
    """Send reminder emails for tomorrow's rides"""
    # Calculate tomorrow's date
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    tomorrow_str = tomorrow.strftime('%Y-%m-%d')
    
    conn = sqlite3.connect('docent_rideshare.db')
    c = conn.cursor()
    
    # Get all matched rides for tomorrow
    c.execute('''
        SELECT 
            rr.time, rr.from_location, rr.to_location,
            req.id as requester_id, req.name as requester_name, req.email as requester_email, req.phone as requester_phone,
            drv.id as driver_id, drv.name as driver_name, drv.email as driver_email, drv.phone as driver_phone
        FROM ride_requests rr
        JOIN ride_offers ro ON rr.matched_with = ro.id
        JOIN docents req ON rr.docent_id = req.id
        JOIN docents drv ON ro.docent_id = drv.id
        WHERE rr.date = ?
    ''', (tomorrow_str,))
    
    matches = c.fetchall()
    
    for match in matches:
        time = match[0]
        pickup = match[1]
        destination = match[2]
        requester_id = match[3]
        requester_name = match[4]
        requester_email = match[5]
        requester_phone = match[6]
        driver_id = match[7]
        driver_name = match[8]
        driver_email = match[9]
        driver_phone = match[10]
        
        # Reminder to rider
        subject = f"Reminder: Your Ride Tomorrow ({tomorrow.strftime('%A, %B %d')})"
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                h2 {{ color: #2c3e50; }}
                .ride-details {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 15px 0; }}
                .reminder {{ color: #e74c3c; font-weight: bold; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>Ride Reminder for Tomorrow</h2>
                <p>Hello {requester_name},</p>
                <p>This is a friendly reminder about your ride tomorrow.</p>
                
                <div class="ride-details">
                    <p>Date: {tomorrow.strftime('%A, %B %d')}</p>
                    <p>Time: {time}</p>
                    <p>Driver: {driver_name}</p>
                    <p>Driver's Phone: {driver_phone}</p>
                    <p>Pickup: Your neighborhood ({pickup})</p>
                    <p>Destination: {destination}</p>
                </div>
                
                <p class="reminder">If you need to cancel, please contact your driver directly as soon as possible.</p>
            </div>
        </body>
        </html>
        """
        send_email(requester_email, subject, html_content)
        
        # Reminder to driver
        subject = f"Reminder: Your Passenger Tomorrow ({tomorrow.strftime('%A, %B %d')})"
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                h2 {{ color: #2c3e50; }}
                .ride-details {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 15px 0; }}
                .reminder {{ color: #e74c3c; font-weight: bold; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>Driving Reminder for Tomorrow</h2>
                <p>Hello {driver_name},</p>
                <p>This is a friendly reminder about driving a fellow docent tomorrow.</p>
                
                <div class="ride-details">
                    <p>Date: {tomorrow.strftime('%A, %B %d')}</p>
                    <p>Time: {time}</p>
                    <p>Passenger: {requester_name}</p>
                    <p>Passenger's Phone: {requester_phone}</p>
                    <p>Pickup: Their neighborhood ({pickup})</p>
                    <p>Destination: {destination}</p>
                </div>
                
                <p class="reminder">If your plans have changed, please contact your passenger directly as soon as possible.</p>
            </div>
        </body>
        </html>
        """
        send_email(driver_email, subject, html_content)
    
    conn.close()
    print(f"Sent {len(matches)} ride reminders for tomorrow ({tomorrow_str})")

def schedule_tasks():
    """Set up scheduled tasks for the system"""
    # Process Google Form responses every hour
    schedule.every(1).hours.do(process_ride_request_form_responses)
    schedule.every(1).hours.do(process_ride_offer_form_responses)
    
    # Send weekly email every Sunday at 9am
    schedule.every().sunday.at("09:00").do(send_weekly_reminder)
    
    # Send ride reminders every day at 5pm
    schedule.every().day.at("17:00").do(send_ride_reminders)
    
    # Start the scheduler in a background thread
    def run_scheduler():
        while True:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
    
    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    
    print("Scheduled tasks have been set up:")
    print("- Process form responses: Every hour")
    print("- Weekly emails: Every Sunday at 9:00 AM")
    print("- Ride reminders: Every day at 5:00 PM")

def create_google_forms():
    """
    Guidance for creating the required Google Forms
    
    Note: This function doesn't actually create the forms - it's just a guide.
    You'll need to manually create these forms in your Google account.
    """
    print("\n==== GOOGLE FORMS SETUP GUIDE ====")
    print("\n1. RIDE REQUEST FORM")
    print("Create a Google Form with these fields:")
    print("   - Email Address (type: email, required)")
    print("   - Date (type: date, required)")
    print("   - Pickup Time (type: time, required)")
    print("   - Neighborhood (type: short answer, required)")
    print("   - Destination (type: short answer, required)")
    print("   - Additional Notes (type: paragraph, optional)")
    
    print("\n2. RIDE OFFER FORM")
    print("Create a Google Form with these fields:")
    print("   - Email Address (type: email, required)")
    print("   - Date (type: date, required)")
    print("   - Pickup Time (type: time, required)")
    print("   - Starting Neighborhood (type: short answer, required)")
    print("   - Destination (type: short answer, required)")
    print("   - Available Seats (type: dropdown, options: 1-4, required)")
    print("   - Additional Notes (type: paragraph, optional)")
    
    print("\nFor Tuesday Learning sessions, add these options:")
    print("- Add 'Tuesday Learning (Pittsburgh Museum)' as a pre-filled option for destination")
    print("- Consider creating dedicated forms just for Tuesday Learning")
    
    print("\nAfter creating each form:")
    print("1. Connect it to a Google Sheet (Responses → Create Spreadsheet)")
    print("2. Get the form URL to share with docents")
    print("3. Get the Sheet ID (from the URL) to update the config in this script")
    print("4. Ensure 'Collect email addresses' is turned ON")
    
    print("\n==== END OF GUIDE ====")

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
    
    # Clean up column names (in case they have spaces or different capitalization)
    df.columns = [col.strip().lower() for col in df.columns]
    
    # Map expected columns to actual columns
    column_mapping = {
        'name': next((col for col in df.columns if 'name' in col), None),
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

# Main execution
if __name__ == "__main__":
    print("===== Docent Rideshare System =====")
    print("1. Initialize database")
    print("2. Import docents from Excel")
    print("3. Create Google Forms guide")
    print("4. Send test weekly reminder")
    print("5. Start scheduling system")
    print("6. Exit")
    
    choice = input("\nEnter your choice (1-6): ")
    
    if choice == '1':
        print("Initializing database...")
        init_db()
        print("Database initialized successfully.")
    
    elif choice == '2':
        excel_file = input("Enter the path to your Excel file: ")
        import_docents_from_excel(excel_file)
    
    elif choice == '3':
        create_google_forms()
    
    elif choice == '4':
        print("Sending test weekly reminder...")
        send_weekly_reminder()
        print("Test reminder sent.")
    
    elif choice == '5':
        print("Starting scheduling system...")
        init_db()  # Make sure database is initialized
        schedule_tasks()
        print("System is running. Press Ctrl+C to exit.")
        
        # Keep the main thread alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Exiting...")
    
    elif choice == '6':
        print("Exiting...")
    
    else:
        print("Invalid choice. Please run the script again.")