from requests import session
from datetime import datetime
from urllib.parse import urlparse
from PIL import Image
import piexif
import os

# Your Procare login info goes here
PROCARE_USERNAME = 'YOUR PROCARE EMAIL'
PROCARE_PASSWORD = 'YOUR PROCARE PASSWORD'

PROCARE_LOGIN_URL = 'https://api-school.procareconnect.com/api/web/auth/'
PROCARE_PHOTO_URL = 'https://api-school.procareconnect.com/api/web/parent/photos/'

# Path for where to download the photos
PHOTO_ROOT_PATH = '.\photos'

# Some date formatting stuff for saving photos later
DT_FORMAT = '%Y-%m-%d %I:%M'
EXIF_DT_FORMAT = '%Y:%m:%d %H:%M:%S'
START_DATE = datetime(2021, 10, 17)


# This is no longer used. I originally saved the pictures in /photos/<year>/<month>
# folders, but it's easier if they all in one directory for OneDrive
def create_year_month_dir(dt):
    year = str(dt.year)
    month = str(dt.month)

    year_dir = os.path.join(PHOTO_ROOT_PATH, year)
    if not os.path.exists(year_dir):
        os.mkdir(year_dir)
    
    month_dir = os.path.join(PHOTO_ROOT_PATH, year, month)
    if not os.path.exists(month_dir):
        os.mkdir(month_dir)

    return month_dir


# Procare does a terrible job of normalizing the filenames
# We clean them up here so that it's easier to check if we've 
# already downloaded a photo
def check_filename_format(filename):
    if filename.find('.') == -1:
        parts = filename.rpartition('_')
        return f'{parts[0]}.{parts[2]}'
    
    return filename

# INITAL SETUP
START = START_DATE.strftime(DT_FORMAT)
END = datetime.now().strftime(DT_FORMAT)

if not os.path.exists(PHOTO_ROOT_PATH):
    os.mkdir(PHOTO_ROOT_PATH)

sesh = session()

# LOGIN
resp = sesh.post(PROCARE_LOGIN_URL, data={'email': PROCARE_USERNAME, 'password': PROCARE_PASSWORD})
auth_token = resp.json().get('user').get('auth_token')
sesh.headers = {'authorization': f'Bearer {auth_token}'}

# MAIN LOOP - Check each photo in procare to see if it's been downloaded or not
# If not downloaded, grab it, add EXIF data for Google Photos and save
total_photos = None
checked_photos = 0
page = 1
while total_photos != checked_photos:
    resp = sesh.get(PROCARE_PHOTO_URL, params={'page': page, 'filters[photo][datetime_from]': START, 'filters[photo][datetime_to]': END})
    resp_data = resp.json()
    if not total_photos:
        total_photos = resp_data.get('total')
        print(f'Starting download of {total_photos} photos')
    

    for photo in resp_data.get('photos'):
        photo_url = photo['main_url']
        filename = urlparse(photo_url).path.split('/')[-1]
        filename = check_filename_format(filename)
        created_date = datetime.fromisoformat(photo['created_at'])
        # photo_dir = create_year_month_dir(created_date)
        filepath = os.path.join(PHOTO_ROOT_PATH, filename)

        if not os.path.exists(filepath):
            print(f'Downloading {PHOTO_ROOT_PATH}\\{filename}')
            with open(filepath, 'wb') as f:
                photo_data = sesh.get(photo_url).content
                f.write(photo_data)
            img = Image.open(filepath)
            exif = piexif.load(img.info['exif'])
            exif['0th'][piexif.ImageIFD.DateTime]= created_date.strftime(EXIF_DT_FORMAT)
            exif['Exif'][piexif.ExifIFD.DateTimeOriginal] = created_date.strftime(EXIF_DT_FORMAT)
            exif['0th'][piexif.ImageIFD.XPTitle]= photo.get('caption', '').encode('utf-16')
            img.save(filepath, exif=piexif.dump(exif))
        else:
            print(f'Already Existed {PHOTO_ROOT_PATH}\\{filename}')
        checked_photos += 1

    page += 1
