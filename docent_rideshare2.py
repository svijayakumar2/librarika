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
EMAIL_ADDRESS = "saranyav196@gmail.com"  # Change to your email
EMAIL_PASSWORD = "your_app_password"  # Change to your app password
SMTP_SERVER = "smtp.gmail.com"  # Change as needed for your provider
SMTP_PORT = 465

# Google Sheets configuration
CREDENTIALS_FILE = "credentials.json"  # Your Google API credentials file
RIDESHARE_SHEET_ID = "your_google_sheet_id"  # Single form response sheet

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
            has_car BOOLEAN
        )
    ''')
    
    # Create rides table - unified table for all ride coordination
    c.execute('''
        CREATE TABLE IF NOT EXISTS rides (
            id TEXT PRIMARY KEY,
            date TEXT,
            is_tuesday_learning BOOLEAN,
            destination TEXT,
            timestamp TEXT
        )
    ''')
    
    # Create ride participants table - connects docents to rides
    c.execute('''
        CREATE TABLE IF NOT EXISTS ride_participants (
            id TEXT PRIMARY KEY,
            ride_id TEXT,
            docent_id TEXT,
            status TEXT,  -- "driving", "riding", "available", "unavailable" 
            pickup_time TEXT,
            seats_offered INTEGER,
            timestamp TEXT,
            FOREIGN KEY (ride_id) REFERENCES rides (id),
            FOREIGN KEY (docent_id) REFERENCES docents (id)
        )
    ''')
    
    # Create ride assignments table - for tracking driver assignments
    c.execute('''
        CREATE TABLE IF NOT EXISTS ride_assignments (
            id TEXT PRIMARY KEY,
            ride_id TEXT,
            driver_id TEXT,
            rider_id TEXT,
            FOREIGN KEY (ride_id) REFERENCES rides (id),
            FOREIGN KEY (driver_id) REFERENCES docents (id),
            FOREIGN KEY (rider_id) REFERENCES docents (id)
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

def get_or_create_ride(date, destination, is_tuesday_learning=False):
    """Get an existing ride or create a new one for the date and destination"""
    conn = sqlite3.connect('docent_rideshare.db')
    c = conn.cursor()
    
    # Check if this ride already exists
    c.execute('''
        SELECT id FROM rides 
        WHERE date = ? AND destination = ? AND is_tuesday_learning = ?
    ''', (date, destination, is_tuesday_learning))
    
    result = c.fetchone()
    
    if result:
        ride_id = result[0]
    else:
        # Create new ride
        ride_id = str(uuid.uuid4())
        timestamp = datetime.datetime.now().isoformat()
        
        c.execute('''
            INSERT INTO rides (id, date, is_tuesday_learning, destination, timestamp)
            VALUES (?, ?, ?, ?, ?)
        ''', (ride_id, date, is_tuesday_learning, destination, timestamp))
        
        conn.commit()
    
    conn.close()
    return ride_id

def process_form_responses():
    """
    Process the unified rideshare form responses from Google Sheet
    """
    try:
        # Connect to the Google Sheet
        client = connect_to_sheets()
        sheet = client.open_by_key(RIDESHARE_SHEET_ID).sheet1
        
        # Get all records
        records = sheet.get_all_records()
        
        # Convert to DataFrame for easier processing
        df = pd.DataFrame(records)
        
        if df.empty:
            print("No form responses to process")
            return
        
        # Connect to database
        conn = sqlite3.connect('docent_rideshare.db')
        cursor = conn.cursor()
        
        # Get timestamp of last processed entry
        cursor.execute('SELECT MAX(timestamp) FROM ride_participants')
        last_timestamp = cursor.fetchone()[0]
        
        # Set a default timestamp if no previous entries
        if not last_timestamp:
            last_timestamp = "1970-01-01T00:00:00"
        
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
                docent_name = docent[1]
                
                # Parse date and other fields
                date_str = row['Date']
                destination = row['Destination']
                
                # Check if this is for Tuesday learning
                is_tuesday_learning = "Tuesday Learning" in destination
                
                # Get participation status
                participation = row['Participation']
                is_driving = "I can drive" in participation
                is_riding = "I need a ride" in participation
                is_unavailable = "I cannot attend" in participation
                
                # Get pickup time if provided
                pickup_time = row.get('Pickup Time', '')
                
                # Get available seats if driving
                seats_offered = 0
                if is_driving:
                    seats_offered = int(row.get('Available Seats', 0))
                
                # Determine status
                if is_driving:
                    status = "driving"
                elif is_riding:
                    status = "riding"
                elif is_unavailable:
                    status = "unavailable"
                else:
                    status = "available"  # Default if none selected
                
                # Get or create the ride
                ride_id = get_or_create_ride(date_str, destination, is_tuesday_learning)
                
                # Generate unique ID for this participant
                participant_id = str(uuid.uuid4())
                
                # Add to ride_participants
                cursor.execute('''
                    INSERT INTO ride_participants 
                    (id, ride_id, docent_id, status, pickup_time, seats_offered, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (participant_id, ride_id, docent_id, status, pickup_time, 
                      seats_offered, timestamp))
                
                new_entries += 1
                
                # Send confirmation email
                subject = "Your Rideshare Participation Has Been Recorded"
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
                        <h2>Your Rideshare Participation</h2>
                        <p>Hello {docent_name},</p>
                        <p>We've recorded your participation for {date_str}.</p>
                        
                        <div class="details">
                            <p>Destination: {destination}</p>
                            <p>Your status: {status.capitalize()}</p>
                            {f"<p>Pickup time: {pickup_time}</p>" if pickup_time else ""}
                            {f"<p>Seats offered: {seats_offered}</p>" if is_driving else ""}
                        </div>
                        
                        <p>We'll coordinate drivers and riders and let you know the arrangements soon!</p>
                        <p>If your plans change, please fill out the form again or call our coordinator at (412) 555-1234.</p>
                    </div>
                </body>
                </html>
                """
                send_email(email, subject, html_content)
        
        # Commit changes
        conn.commit()
        conn.close()
        
        print(f"Processed {new_entries} new form responses")
        
        # Try to match rides if new entries
        if new_entries > 0:
            assign_drivers_to_rides()
    
    except Exception as e:
        print(f"Error processing form responses: {e}")

def assign_drivers_to_rides():
    """
    Assign drivers to riders based on neighborhood proximity and taking turns driving
    """
    conn = sqlite3.connect('docent_rideshare.db')
    c = conn.cursor()
    
    # Get all rides that need assignment (have both drivers and riders)
    c.execute('''
        SELECT r.id, r.date, r.destination 
        FROM rides r
        WHERE EXISTS (
            SELECT 1 FROM ride_participants rp 
            WHERE rp.ride_id = r.id AND rp.status = 'driving'
        )
        AND EXISTS (
            SELECT 1 FROM ride_participants rp 
            WHERE rp.ride_id = r.id AND rp.status = 'riding'
        )
        AND NOT EXISTS (
            SELECT 1 FROM ride_assignments ra 
            WHERE ra.ride_id = r.id
        )
    ''')
    
    rides_to_assign = c.fetchall()
    
    for ride in rides_to_assign:
        ride_id = ride[0]
        ride_date = ride[1]
        destination = ride[2]
        
        # Get all drivers for this ride
        c.execute('''
            SELECT rp.docent_id, rp.pickup_time, rp.seats_offered, d.neighborhood 
            FROM ride_participants rp
            JOIN docents d ON rp.docent_id = d.id
            WHERE rp.ride_id = ? AND rp.status = 'driving'
            ORDER BY rp.seats_offered DESC  -- Prioritize drivers with more seats
        ''', (ride_id,))
        
        drivers = c.fetchall()
        
        # Get all riders for this ride
        c.execute('''
            SELECT rp.docent_id, d.neighborhood, d.name, d.email, d.phone
            FROM ride_participants rp
            JOIN docents d ON rp.docent_id = d.id
            WHERE rp.ride_id = ? AND rp.status = 'riding'
        ''', (ride_id,))
        
        riders = c.fetchall()
        
        # Match riders to drivers
        assignments = []
        assigned_riders = set()
        
        for driver in drivers:
            driver_id = driver[0]
            driver_time = driver[1]
            driver_seats = driver[2]
            driver_neighborhood = driver[3]
            
            # Get driver details
            c.execute('SELECT name, email, phone FROM docents WHERE id = ?', (driver_id,))
            driver_details = c.fetchone()
            driver_name = driver_details[0]
            driver_email = driver_details[1]
            driver_phone = driver_details[2]
            
            # Find riders in the same or nearby neighborhoods
            available_seats = driver_seats
            assigned_to_this_driver = []
            
            # First pass: try to match by same neighborhood
            for rider in riders:
                if available_seats <= 0:
                    break
                    
                rider_id = rider[0]
                rider_neighborhood = rider[1]
                rider_name = rider[2]
                rider_email = rider[3]
                rider_phone = rider[4]
                
                if rider_id in assigned_riders:
                    continue
                
                if rider_neighborhood == driver_neighborhood:
                    # Create assignment
                    assignment_id = str(uuid.uuid4())
                    assignments.append((assignment_id, ride_id, driver_id, rider_id))
                    assigned_riders.add(rider_id)
                    available_seats -= 1
                    assigned_to_this_driver.append((rider_name, rider_email, rider_phone, rider_neighborhood))
            
            # Second pass: assign remaining riders to drivers with available seats
            for rider in riders:
                if available_seats <= 0:
                    break
                    
                rider_id = rider[0]
                rider_neighborhood = rider[1]
                rider_name = rider[2]
                rider_email = rider[3]
                rider_phone = rider[4]
                
                if rider_id in assigned_riders:
                    continue
                
                # Create assignment
                assignment_id = str(uuid.uuid4())
                assignments.append((assignment_id, ride_id, driver_id, rider_id))
                assigned_riders.add(rider_id)
                available_seats -= 1
                assigned_to_this_driver.append((rider_name, rider_email, rider_phone, rider_neighborhood))
            
            # Send email to driver with their assigned riders
            if assigned_to_this_driver:
                subject = f"Your Driving Assignment for {ride_date}"
                
                # Build the HTML table of riders
                riders_html = ""
                for rider in assigned_to_this_driver:
                    r_name, r_email, r_phone, r_neighborhood = rider
                    riders_html += f"""
                    <tr>
                        <td>{r_name}</td>
                        <td>{r_phone}</td>
                        <td>{r_neighborhood}</td>
                    </tr>
                    """
                
                html_content = f"""
                <html>
                <head>
                    <style>
                        body {{ font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; }}
                        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                        h2 {{ color: #2c3e50; }}
                        .ride-details {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 15px 0; }}
                        table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
                        th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }}
                        th {{ background-color: #f2f2f2; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h2>Your Driving Assignment</h2>
                        <p>Hello {driver_name},</p>
                        <p>Thank you for offering to drive on {ride_date}.</p>
                        
                        <div class="ride-details">
                            <p>Destination: {destination}</p>
                            <p>Pickup time: {driver_time}</p>
                            <p>You have been assigned {len(assigned_to_this_driver)} passenger(s):</p>
                            
                            <table>
                                <tr>
                                    <th>Passenger</th>
                                    <th>Phone</th>
                                    <th>Neighborhood</th>
                                </tr>
                                {riders_html}
                            </table>
                        </div>
                        
                        <p>Please contact your passengers to coordinate exact pickup locations and times.</p>
                        <p>If your plans change, please call our coordinator at (412) 555-1234 as soon as possible.</p>
                    </div>
                </body>
                </html>
                """
                send_email(driver_email, subject, html_content)
        
        # Save all assignments
        for assignment in assignments:
            c.execute('''
                INSERT INTO ride_assignments (id, ride_id, driver_id, rider_id)
                VALUES (?, ?, ?, ?)
            ''', assignment)
        
        # Send email to riders
        for rider_id in assigned_riders:
            # Get rider details
            c.execute('SELECT name, email FROM docents WHERE id = ?', (rider_id,))
            rider = c.fetchone()
            rider_name = rider[0]
            rider_email = rider[1]
            
            # Get assigned driver
            c.execute('''
                SELECT d.name, d.phone, d.neighborhood, rp.pickup_time
                FROM ride_assignments ra
                JOIN docents d ON ra.driver_id = d.id
                JOIN ride_participants rp ON rp.docent_id = d.id AND rp.ride_id = ra.ride_id
                WHERE ra.rider_id = ? AND ra.ride_id = ?
            ''', (rider_id, ride_id))
            
            assignment = c.fetchone()
            driver_name = assignment[0]
            driver_phone = assignment[1]
            driver_neighborhood = assignment[2]
            pickup_time = assignment[3]
            
            subject = f"Your Ride Assignment for {ride_date}"
            html_content = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                    h2 {{ color: #2c3e50; }}
                    .ride-details {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 15px 0; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h2>Your Ride Assignment</h2>
                    <p>Hello {rider_name},</p>
                    <p>Good news! We've matched you with a driver for {ride_date}.</p>
                    
                    <div class="ride-details">
                        <p>Destination: {destination}</p>
                        <p>Driver: {driver_name}</p>
                        <p>Driver's Phone: {driver_phone}</p>
                        <p>Driver's Neighborhood: {driver_neighborhood}</p>
                        <p>Approximate Pickup Time: {pickup_time}</p>
                    </div>
                    
                    <p>Your driver will contact you to coordinate the exact pickup location and time.</p>
                    <p>If your plans change, please contact your driver and our coordinator at (412) 555-1234 as soon as possible.</p>
                </div>
            </body>
            </html>
            """
            send_email(rider_email, subject, html_content)
    
    conn.commit()
    conn.close()
    print(f"Processed driver assignments for {len(rides_to_assign)} rides")

def send_weekly_reminder():
    """Send weekly email with link to Google Form for the next Tuesday"""
    conn = sqlite3.connect('docent_rideshare.db')
    c = conn.cursor()
    
    # Get all docents
    c.execute('SELECT id, name, email, has_car, neighborhood FROM docents')
    docents = c.fetchall()
    
    # Get next Tuesday date
    tuesday_next = get_next_tuesday()
    tuesday_str = tuesday_next.strftime('%B %d, %Y')
    tuesday_day = tuesday_next.strftime('%A, %B %d')
    
    # Google Form URL
    RIDESHARE_FORM_URL = "https://forms.gle/your-unified-form-id"
    
    for docent in docents:
        docent_id = docent[0]
        name = docent[1]
        email = docent[2]
        has_car = docent[3]
        neighborhood = docent[4]
        
        # Create personalized email with form link
        subject = f"Docent Rideshare for Tuesday, {tuesday_day}"
        
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                h1, h2, h3 {{ color: #2c3e50; }}
                .tuesday-box {{ background-color: #e8f4f8; padding: 15px; border-radius: 5px; margin: 20px 0; border: 1px solid #b3d7e5; }}
                .button {{ display: inline-block; padding: 10px 20px; background-color: #3498db; color: white; text-decoration: none; 
                        border-radius: 5px; font-size: 16px; margin: 10px 5px 10px 0; }}
                .note {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Hello {name}!</h1>
                <h2>Docent Rideshare for Next Week</h2>
                
                <div class="tuesday-box">
                    <h3>Tuesday Learning Session: {tuesday_day}</h3>
                    <p>Our weekly learning session is at the Pittsburgh Museum at 10:30 AM.</p>
                    <p>Please let us know if you'll be attending and whether you can drive or need a ride:</p>
                    
                    <a href="{RIDESHARE_FORM_URL}" class="button">
                        Fill Out Rideshare Form
                    </a>
                </div>
                
                <div class="note">
                    <h3>Taking Turns Driving</h3>
                    <p>Our goal is to have everyone take turns driving when possible. This system helps us coordinate who should drive each week to make sure the responsibility is shared fairly.</p>
                    <p>Even if you have a car, you can request to be a passenger if you've recently taken a turn driving.</p>
                </div>
                
                <p>Thank you for participating in our ridesharing program!</p>
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
    
    # Get all rides for tomorrow
    c.execute('''
        SELECT r.id, r.destination
        FROM rides r
        WHERE r.date = ?
        AND EXISTS (SELECT 1 FROM ride_assignments ra WHERE ra.ride_id = r.id)
    ''', (tomorrow_str,))
    
    tomorrow_rides = c.fetchall()
    
    for ride in tomorrow_rides:
        ride_id = ride[0]
        destination = ride[1]
        
        # Get all drivers and their passengers
        c.execute('''
            SELECT 
                d.id as driver_id, 
                d.name as driver_name, 
                d.email as driver_email,
                d.phone as driver_phone,
                rp.pickup_time
            FROM ride_participants rp
            JOIN docents d ON rp.docent_id = d.id
            WHERE rp.ride_id = ? AND rp.status = 'driving'
            AND EXISTS (
                SELECT 1 FROM ride_assignments ra 
                WHERE ra.ride_id = rp.ride_id AND ra.driver_id = d.id
            )
        ''', (ride_id,))
        
        drivers = c.fetchall()
        
        for driver in drivers:
            driver_id = driver[0]
            driver_name = driver[1]
            driver_email = driver[2]
            driver_phone = driver[3]
            pickup_time = driver[4]
            
            # Get passengers for this driver
            c.execute('''
                SELECT 
                    p.name as passenger_name, 
                    p.email as passenger_email,
                    p.phone as passenger_phone,
                    p.neighborhood
                FROM ride_assignments ra
                JOIN docents p ON ra.rider_id = p.id
                WHERE ra.ride_id = ? AND ra.driver_id = ?
            ''', (ride_id, driver_id))
            
            passengers = c.fetchall()
            
            # Build passenger table HTML
            passengers_html = ""
            for passenger in passengers:
                passenger_name = passenger[0]
                passenger_phone = passenger[2]
                passenger_neighborhood = passenger[3]
                
                passengers_html += f"""
                <tr>
                    <td>{passenger_name}</td>
                    <td>{passenger_phone}</td>
                    <td>{passenger_neighborhood}</td>
                </tr>
                """
            
            # Send reminder to driver
            subject = f"Reminder: You're Driving Tomorrow ({tomorrow.strftime('%A, %B %d')})"
            html_content = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                    h2 {{ color: #2c3e50; }}
                    .ride-details {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 15px 0; }}
                    table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
                    th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }}
                    th {{ background-color: #f2f2f2; }}
                    .reminder {{ color: #e74c3c; font-weight: bold; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h2>Driving Reminder for Tomorrow</h2>
                    <p>Hello {driver_name},</p>
                    <p>This is a friendly reminder that you're scheduled to drive tomorrow.</p>
                    
                    <div class="ride-details">
                        <p>Date: {tomorrow.strftime('%A, %B %d')}</p>
                        <p>Destination: {destination}</p>
                        <p>Pickup time: {pickup_time}</p>
                        
                        <p>Your passengers:</p>
                        <table>
                            <tr>
                                <th>Passenger</th>
                                <th>Phone</th>
                                <th>Neighborhood</th>
                            </tr>
                            {passengers_html}
                        </table>
                    </div>
                    
                    <p class="reminder">If your plans have changed, please contact your passengers and our coordinator at (412) 555-1234 as soon as possible.</p>
                </div>
            </body>
            </html>
            """
            send_email(driver_email, subject, html_content)
            
            # Send reminder to each passenger
            for passenger in passengers:
                passenger_name = passenger[0]
                passenger_email = passenger[1]
                
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
                        <p>Hello {passenger_name},</p>
                        <p>This is a friendly reminder about your ride tomorrow.</p>
                        
                        <div class="ride-details">
                            <p>Date: {tomorrow.strftime('%A, %B %d')}</p>
                            <p>Destination: {destination}</p>
                            <p>Driver: {driver_name}</p>
                            <p>Driver's Phone: {driver_phone}</p>
                            <p>Pickup Time: {pickup_time}</p>
                        </div>
                        
                        <p class="reminder">If you need to cancel, please contact your driver directly as soon as possible.</p>
                    </div>
                </body>
                </html>
                """
                send_email(passenger_email, subject, html_content)
    
    conn.close()
    print(f"Sent ride reminders for {len(tomorrow_rides)} rides scheduled for tomorrow ({tomorrow_str})")

def schedule_jobs():
    """Schedule all recurring jobs"""
    # Process form responses every hour
    schedule.every().hour.do(process_form_responses)
    
    # Send weekly reminders every Monday at 9am
    schedule.every().monday.at("09:00").do(send_weekly_reminder)
    
    # Send ride reminders every day at 6pm for next day's rides
    schedule.every().day.at("18:00").do(send_ride_reminders)
    
    # Keep the scheduler running
    while True:
        schedule.run_pending()
        time.sleep(60)

def main():
    """Main function to initialize and run the application"""
    # Initialize database
    init_db()
    
    # Start the scheduler in a separate thread
    scheduler_thread = threading.Thread(target=schedule_jobs)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    
    print("Docent Rideshare Coordinator is running...")
    print("Press Ctrl+C to exit")
    
    # Keep the main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")

if __name__ == "__main__":
    main()