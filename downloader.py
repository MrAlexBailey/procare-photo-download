import httpx
import asyncio
import os
import re
from pathlib import Path
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from dateutil.parser import parse as parse_date
import piexif
from gooey import Gooey, GooeyParser


total_photos = 0
photos_processed = 0
# Procare does a terrible job of normalizing the filenames
# We clean them up here so that it's easier to check if we've 
# already downloaded a photo
def check_filename_format(filename):
    if filename.find('.') == -1:
        parts = filename.rpartition('_')
        return f'{parts[0]}.{parts[2]}'
    
    return filename

async def download_photos(client: httpx.AsyncClient, photo_urls: list, headers: dict, save_dir: str):
    """Download and save photos from a list of URLs."""
    Path(save_dir).mkdir(exist_ok=True)
    tasks = []
    for url in photo_urls:
        tasks.append(download_single_photo(client, url, headers, save_dir))
    await asyncio.gather(*tasks)

async def download_single_photo(client: httpx.AsyncClient, photo: dict, headers: dict, save_dir: str):
    """Download a single photo, save it, and update EXIF data, skipping if it exists."""
    global total_photos, photos_processed
    url = photo.get("main_url")
    created_date = photo.get("created_date")
    caption = photo.get("caption")
    
    try:
        # Extract filename from URL (e.g., .../main/photo12402.jpg?1209381038 -> photo12402.jpg)
        match = re.search(r'/main/([^?]+)', url)
        filename = match.group(1) if match else f"photo_{hash(url)}.jpg"
        filename = check_filename_format(filename)
        file_path = os.path.join(save_dir, filename)

        # Skip if file already exists
        if os.path.exists(file_path):
            print(f"Skipped photo: {file_path} (already exists)")
            photos_processed += 1
            print(f"Progress: {photos_processed}/{total_photos}")
            return

        # Download the photo
        response = await client.get(url, headers=headers, timeout=30.0)
        response.raise_for_status()
        with open(file_path, "wb") as f:
            f.write(response.content)

        # Update EXIF data
        try:
            # Parse created_date to EXIF format (YYYY:MM:DD HH:MM:SS)
            if created_date:
                dt = parse_date(created_date)
                exif_date = dt.strftime("%Y:%m:%d %H:%M:%S")
            else:
                exif_date = datetime.now().strftime("%Y:%m:%d %H:%M:%S")  # Fallback

            # Prepare EXIF data
            exif_dict = {"0th": {}, "Exif": {}}
            if exif_date:
                exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = exif_date.encode("utf-8")
            if caption:
                exif_dict["0th"][piexif.ImageIFD.ImageDescription] = caption.encode("utf-8")[:2000]  # Max 2000 bytes

            # Write EXIF data to the file
            piexif.insert(piexif.dump(exif_dict), file_path)
        except Exception as e:
            print(f"Failed to update EXIF for {file_path}: {str(e)}")

        photos_processed += 1
        print(f"Progress: {photos_processed}/{total_photos}")
        print(f"Saved photo: {file_path}")
    except httpx.HTTPStatusError as e:
        print(f"Failed to download {url}: HTTP {e.response.status_code}")
    except Exception as e:
        print(f"Failed to download {url}: {str(e)}")

async def fetch_photos_for_date_range(
    client: httpx.AsyncClient,
    photos_url: str,
    headers: dict,
    datetime_from: datetime,
    datetime_to: datetime
) -> list:
    """Fetch all photo URLs for a given date range across paginated responses."""
    all_photos = []
    page = 1

    while True:
        try:
            params = {
                "page": page,
                "filters[photo][datetime_from]": datetime_from.strftime("%Y-%m-%d %I:%M"),
                "filters[photo][datetime_to]": datetime_to.strftime("%Y-%m-%d %I:%M")
            }
            response = await client.get(photos_url, headers=headers, params=params, timeout=30.0)
            response.raise_for_status()
            data = response.json()
            photos = data.get("photos", [])
            total = data.get("total", 0)
            per_page = data.get("per_page", 50)
            # Collect photo objects with main_url, created_date, and caption
            photo_objects = [
                {"main_url": photo["main_url"], "created_date": photo.get("created_at"), "caption": photo.get("caption")}
                for photo in photos if "main_url" in photo
            ]
            all_photos.extend(photo_objects)

            print(f"Page {page} for {datetime_from.date()} to {datetime_to.date()}: {len(photo_objects)} photos")

            if page * per_page >= total:
                break
            page += 1

        except httpx.HTTPStatusError as e:
            print(f"HTTP Error on page {page} for {datetime_from.date()} to {datetime_to.date()}: {e.response.status_code}")
            break
        except Exception as e:
            print(f"Error on page {page} for {datetime_from.date()} to {datetime_to.date()}: {str(e)}")
            break

    return all_photos

async def run_download(email: str, password: str, start_datetime: datetime, save_dir: str):
    global total_photos
    """Core async function to handle login, photo fetching, and downloading."""
    auth_url = 'https://online-auth.procareconnect.com/sessions/'
    photos_url = 'https://api-school.procareconnect.com/api/web/parent/photos/'
    payload = {
        "email": email,
        "password": password,
        "platform": "web",
        "role": "carer"
    }
    datetime_to = datetime.now() + timedelta(days=1)
    datetime_to = datetime_to.replace(hour=0, minute=0, second=0, microsecond=0)

    async with httpx.AsyncClient(http2=True, verify=True, limits=httpx.Limits(max_connections=20)) as client:
        try:
            # Step 1: Log in
            login_response = await client.post(auth_url, json=payload, timeout=30.0)
            login_response.raise_for_status()
            login_data = login_response.json()
            token = login_data.get("auth_token")
            if not token:
                print("Error: No token found in login response")
                return
            print("Login successful, Token:", token)

            headers = {"Authorization": f"Bearer {token}"}
            all_photo_urls = []

            # Step 2: Iterate month-by-month backward
            current_to = datetime_to
            while current_to > start_datetime:
                current_from = max(
                    start_datetime,
                    current_to - relativedelta(months=1)
                )
                print(f"Getting photo list from {current_from.date()} to {current_to.date()}")

                photo_urls = await fetch_photos_for_date_range(
                    client, photos_url, headers, current_from, current_to
                )
                all_photo_urls.extend(photo_urls)
                current_to = current_from

            if not all_photo_urls:
                print("No photo URLs found")
                return

            print(f"Total photo URLs to download: {len(all_photo_urls)}")
            total_photos = len(all_photo_urls)

            # Step 3: Download photos
            await download_photos(client, all_photo_urls, headers, save_dir)

        except httpx.HTTPStatusError as e:
            print(f"HTTP Error: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            print(f"Error: {str(e)}")

@Gooey(
    program_name="Photo Downloader",
    default_size=(800, 600),
    required_cols=1,
    optional_cols=1,
    progress_regex=r"^Progress: (?P<current>\d+)/(?P<total>\d+)$",
    progress_expr="current / total * 100",
    hide_progress_msg=True,
    show_success_modal=True,
    show_failure_modal=True
)
def main():
    parser = GooeyParser(description="Download photos from example.com API")
    parser.add_argument(
        "--email",
        help="Email for login",
        widget="TextField",
        required=True
    )
    parser.add_argument(
        "--password",
        help="Password for login",
        widget="PasswordField",
        required=True
    )
    parser.add_argument(
        "--start_date",
        help="Start date for photos (YYYY-MM-DD)",
        widget="DateChooser",
        required=True,
        gooey_options={
            "default": datetime(2024, 1, 1).strftime("%Y-%m-%d")
        }
    )
    parser.add_argument(
        "--save_dir",
        help="Folder to save photos",
        widget="DirChooser",
        required=True,
        gooey_options={
            "default": os.path.join(os.getcwd(), "downloaded_photos")
        }
    )

    args = parser.parse_args()

    try:
        start_datetime = datetime.strptime(args.start_date, "%Y-%m-%d")
    except ValueError:
        print("Error: Invalid start date format. Use YYYY-MM-DD.")
        return

    # Ensure save_dir is writable
    try:
        Path(args.save_dir).mkdir(exist_ok=True)
        test_file = os.path.join(args.save_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
    except Exception as e:
        print(f"Error: Cannot write to save directory {args.save_dir}: {str(e)}")
        return

    # Run async function
    asyncio.run(run_download(args.email, args.password, start_datetime, args.save_dir))

if __name__ == "__main__":
    main()