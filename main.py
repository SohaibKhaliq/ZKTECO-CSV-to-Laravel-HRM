from datetime import datetime, timedelta
import json
import requests
import os
import time
import pytz
import backoff
import csv
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration with environment variable support and validation
DEVICE_IP = os.getenv('DEVICE_IP', '10.5.8.3')
DEVICE_PORT = int(os.getenv('DEVICE_PORT', '4370'))
AUTH_MIDDLEWARE = os.getenv('AUTH_MIDDLEWARE')
HR_API_BASE_URL = os.getenv('HR_API_BASE_URL', 'https://hrm.zentacode.com/api')
CHECK_IN_ENDPOINT = os.getenv('CHECK_IN_ENDPOINT', f'{HR_API_BASE_URL}/check-in')
CHECK_OUT_ENDPOINT = os.getenv('CHECK_OUT_ENDPOINT', f'{HR_API_BASE_URL}/check-out')
CSV_FILE = os.getenv('CSV_FILE', 'Timeset Attendance.csv')
TIMEZONE = os.getenv('TIMEZONE', 'Asia/Karachi')
DEFAULT_API_DELAY = float(os.getenv('DEFAULT_API_DELAY', '1.0'))
MAX_RETRY_ATTEMPTS = int(os.getenv('MAX_RETRY_ATTEMPTS', '3'))

# Validate critical configuration
if not AUTH_MIDDLEWARE:
    print("❌ ERROR: AUTH_MIDDLEWARE not configured. Please set it in .env file")
    sys.exit(1)

HR_API_ENDPOINT = f"{HR_API_BASE_URL}/get-last-attendance?auth_middleware={AUTH_MIDDLEWARE}"

# Output files
ALL_ATTENDANCE_FILE = "all_attendance_records.json"
DEVICE_OUTPUT_FILE = "last_attendance_from_device.json"
API_OUTPUT_FILE = "last_attendance_from_api.json"
ATTENDANCE_BY_DATE_FILE = "attendance_by_date.json"
FILTERED_ATTENDANCE_FILE = "filtered_attendance_by_date.json"
API_CALL_LOGS_FILE = "api_call_logs.json"
PROCESSED_DATES_FILE = "processed_dates.json"
CSV_FILE = "Timeset Attendance.csv"

def validate_csv_file():
    """
    Validates that the CSV file exists and is readable.
    Returns True if valid, False otherwise.
    """
    if not os.path.exists(CSV_FILE):
        print(f"❌ ERROR: CSV file '{CSV_FILE}' not found")
        return False
    
    if not os.access(CSV_FILE, os.R_OK):
        print(f"❌ ERROR: CSV file '{CSV_FILE}' is not readable")
        return False
    
    try:
        with open(CSV_FILE, 'r') as f:
            # Try to read first line to validate it's a valid file
            first_line = f.readline()
            if not first_line:
                print(f"❌ ERROR: CSV file '{CSV_FILE}' is empty")
                return False
    except Exception as e:
        print(f"❌ ERROR: Cannot read CSV file '{CSV_FILE}': {str(e)}")
        return False
    
    print(f"✅ CSV file '{CSV_FILE}' validated successfully")
    return True

def append_to_json_file(filepath, new_data):
    """
    Appends data to a JSON file safely (handles both list and dict formats).
    """
    data = []

    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                existing = json.load(f)
                if isinstance(existing, list):
                    data = existing
                elif isinstance(existing, dict):
                    data = [existing]
        except (json.JSONDecodeError, FileNotFoundError, PermissionError) as e:
            print(f"⚠️ Error reading {filepath}: {str(e)}. Starting with empty list.")
            data = []

    if isinstance(new_data, list):
        data.extend(new_data)
    else:
        data.append(new_data)

    try:
        with open(filepath, "w") as f:
            json.dump(data, f, indent=4)
    except PermissionError as e:
        print(f"❌ Permission denied writing to {filepath}: {str(e)}")
        raise
    except Exception as e:
        print(f"❌ Error writing to {filepath}: {str(e)}")
        raise

def update_processed_dates(date_str, status, success_count, failure_count):
    """
    Updates processed_dates.json with the processing status for a date.
    """
    processed = {}
    if os.path.exists(PROCESSED_DATES_FILE):
        try:
            with open(PROCESSED_DATES_FILE, "r") as f:
                processed = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError, PermissionError):
            processed = {}

    processed[date_str] = {
        "status": status,
        "success_count": success_count,
        "failure_count": failure_count,
        "processed_at": datetime.now(pytz.timezone(TIMEZONE)).isoformat()
    }

    try:
        with open(PROCESSED_DATES_FILE, "w") as f:
            json.dump(processed, f, indent=4)
    except Exception as e:
        print(f"❌ Error writing to {PROCESSED_DATES_FILE}: {str(e)}")
        raise

def is_date_processed(date_str):
    """
    Checks if a date was successfully processed with at least one successful API call.
    """
    if not os.path.exists(PROCESSED_DATES_FILE):
        return False
    try:
        with open(PROCESSED_DATES_FILE, "r") as f:
            processed = json.load(f)
        if date_str in processed:
            return processed[date_str].get("status") == "success" and processed[date_str].get("success_count", 0) > 0
        return False
    except (json.JSONDecodeError, FileNotFoundError, PermissionError):
        return False

def log_to_file(filepath, operation, level, message, additional_data):
    """
    Logs a message to a JSON file with operation, level, message, and additional data.
    """
    log_entry = {
        "operation": operation,
        "level": level,
        "message": message,
        "additional_data": additional_data,
        "logged_at": datetime.now(pytz.timezone(TIMEZONE)).isoformat()
    }
    append_to_json_file(filepath, log_entry)
    print(f"[{level.upper()}] {operation}: {message}")

def fetch_all_attendance_from_csv():
    """
    Fetches all attendance records from the CSV file and saves them.
    """
    try:
        all_records = []
        with open(CSV_FILE, 'r') as f:
            reader = csv.reader(f, delimiter='\t')
            for row in reader:
                if len(row) < 6:
                    continue
                user_id = row[0].strip()
                if not user_id.isdigit():
                    continue
                timestamp = row[1].strip()
                try:
                    datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                punch = int(row[3].strip())
                status = int(row[4].strip())
                method = "palm" if status == 25 else "finger"
                record = {
                    "user_id": user_id,
                    "timestamp": timestamp,
                    "status": status,
                    "punch": punch,
                    "method": method,
                    "fetched_at": datetime.now(pytz.timezone(TIMEZONE)).isoformat()
                }
                all_records.append(record)

        if not all_records:
            print("⚠️ No attendance records found.")
            return None

        append_to_json_file(ALL_ATTENDANCE_FILE, all_records)
        print(f"✅ {len(all_records)} attendance records saved to {ALL_ATTENDANCE_FILE}.")
        return all_records

    except Exception as e:
        print("❌ Error while fetching all attendance from CSV:", e)
        return None

def fetch_last_attendance_from_csv():
    """
    Fetches the last attendance record from the CSV file and overwrites it in last_attendance_from_device.json.
    """
    try:
        all_records = []
        with open(CSV_FILE, 'r') as f:
            reader = csv.reader(f, delimiter='\t')
            for row in reader:
                if len(row) < 6:
                    continue
                user_id = row[0].strip()
                if not user_id.isdigit():
                    continue
                timestamp = row[1].strip()
                try:
                    dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                punch = int(row[3].strip())
                status = int(row[4].strip())
                method = "palm" if status == 25 else "finger"
                record = {
                    "user_id": user_id,
                    "timestamp": timestamp,
                    "status": status,
                    "punch": punch,
                    "method": method,
                    "fetched_at": datetime.now(pytz.timezone(TIMEZONE)).isoformat()
                }
                all_records.append(record)

        if not all_records:
            print("⚠️ No attendance records found.")
            return None

        last_record = max(all_records, key=lambda x: datetime.strptime(x["timestamp"], "%Y-%m-%d %H:%M:%S"))

        try:
            with open(DEVICE_OUTPUT_FILE, "w") as f:
                json.dump(last_record, f, indent=4)
            print("✅ Last attendance record overwritten in", DEVICE_OUTPUT_FILE)
        except Exception as e:
            print(f"❌ Error writing to {DEVICE_OUTPUT_FILE}: {str(e)}")
            return None

        return last_record

    except Exception as e:
        print("❌ Error while fetching from CSV:", e)
        return None

def get_from_hr_api():
    """
    Fetches the last attendance record from HR API and overwrites it in API_OUTPUT_FILE.
    Returns the in_date for reference.
    """
    try:
        response = requests.get(HR_API_ENDPOINT, timeout=30)
        response.raise_for_status()
        json_data = response.json()

        if not json_data.get("data"):
            raise ValueError("No data in HR API response")

        in_date = json_data.get("data", {}).get("in_date")
        if not in_date:
            raise ValueError("No 'in_date' in HR API response")

        try:
            datetime.strptime(in_date, "%Y-%m-%d")
        except ValueError as e:
            print(f"❌ Invalid in_date format: {in_date}")
            return None

        record = {
            "data": json_data,
            "fetched_at": datetime.now(pytz.timezone(TIMEZONE)).isoformat()
        }

        with open(API_OUTPUT_FILE, "w") as f:
            json.dump(record, f, indent=4)
        print("✅ Response from HR API overwritten in", API_OUTPUT_FILE)
        print(json.dumps(record, indent=4))
        return in_date

    except requests.exceptions.Timeout:
        print("❌ Request timeout while accessing HR API")
        return None
    except requests.exceptions.ConnectionError:
        print("❌ Connection error while accessing HR API")
        return None
    except requests.exceptions.HTTPError as http_err:
        print("❌ HTTP Error:", http_err)
        return None
    except Exception as e:
        print("❌ Error accessing HR API:", e)
        return None

def fetch_csv_attendance_by_date(target_date):
    """
    Fetches all attendance records from the CSV for the given date
    and saves them in a separate JSON file.
    """
    try:
        filtered = []
        with open(CSV_FILE, 'r') as f:
            reader = csv.reader(f, delimiter='\t')
            target_date_obj = datetime.strptime(target_date, "%Y-%m-%d").date()
            for row in reader:
                if len(row) < 6:
                    continue
                user_id = row[0].strip()
                if not user_id.isdigit():
                    continue
                timestamp = row[1].strip()
                try:
                    timestamp_dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                    if timestamp_dt.date() != target_date_obj:
                        continue
                except ValueError:
                    continue
                punch = int(row[3].strip())
                status = int(row[4].strip())
                method = "palm" if status == 25 else "finger"
                filtered.append({
                    "user_id": user_id,
                    "timestamp": timestamp,
                    "status": status,
                    "punch": punch,
                    "method": method,
                    "fetched_at": datetime.now(pytz.timezone(TIMEZONE)).isoformat()
                })

        if not filtered:
            print(f"⚠️ No records found on {target_date}.")
        else:
            append_to_json_file(ATTENDANCE_BY_DATE_FILE, filtered)
            print(f"✅ {len(filtered)} record(s) fetched for {target_date} and saved.")

        return filtered

    except Exception as e:
        print(f"❌ Error fetching records for {target_date}:", e)
        return []

def filter_device_attendance_by_date(search_date):
    """
    Searches for attendance records for a specific date in all_attendance_records.json.
    If no records are found, fetches from CSV and saves to filtered_attendance_by_date.json.
    Removes duplicates based on user_id, timestamp, status, punch, and method.
    Truncates filtered_attendance_by_date.json before saving new data.
    """
    try:
        filtered_records = []
        unique_records = set()

        if os.path.exists(ALL_ATTENDANCE_FILE):
            with open(ALL_ATTENDANCE_FILE, "r") as f:
                all_records = json.load(f)

            search_date_obj = datetime.strptime(search_date, "%Y-%m-%d").date()

            for record in all_records:
                timestamp_dt = datetime.strptime(record["timestamp"], "%Y-%m-%d %H:%M:%S")
                record_date = timestamp_dt.date()
                if record_date == search_date_obj:
                    record_tuple = (
                        record["user_id"],
                        record["timestamp"],
                        record["status"],
                        record["punch"],
                        record["method"]
                    )
                    if record_tuple not in unique_records:
                        unique_records.add(record_tuple)
                        filtered_records.append(record)

        if filtered_records:
            with open(FILTERED_ATTENDANCE_FILE, "w") as f:
                json.dump(filtered_records, f, indent=4)
            print(f"✅ {len(unique_records)} unique attendance record(s) found for {search_date} in {ALL_ATTENDANCE_FILE} and saved to {FILTERED_ATTENDANCE_FILE}.")
            print(json.dumps(filtered_records, indent=4))
        else:
            print(f"⚠️ No attendance records found for {search_date} in {ALL_ATTENDANCE_FILE}.")
            print(f"Attempting to fetch records for {search_date} from CSV...")
            filtered_records = fetch_csv_attendance_by_date(search_date)
            if filtered_records:
                unique_records = set()
                deduped_records = []
                for record in filtered_records:
                    record_tuple = (
                        record["user_id"],
                        record["timestamp"],
                        record["status"],
                        record["punch"],
                        record["method"]
                    )
                    if record_tuple not in unique_records:
                        unique_records.add(record_tuple)
                        deduped_records.append(record)
                if deduped_records:
                    with open(FILTERED_ATTENDANCE_FILE, "w") as f:
                        json.dump(deduped_records, f, indent=4)
                    print(f"✅ {len(deduped_records)} unique record(s) fetched from CSV for {search_date} and saved to {FILTERED_ATTENDANCE_FILE}.")
                    print(json.dumps(deduped_records, indent=4))
                else:
                    print(f"⚠️ No unique records found in CSV for {search_date}.")
            else:
                print(f"⚠️ No records found in CSV for {search_date}.")

        return filtered_records

    except Exception as e:
        print(f"❌ Error searching attendance for {search_date}:", e)
        return []

def check_existing_checkin(user_id, date):
    """
    Checks if a check-in exists for the given user_id and date in the HR system.
    Returns True if a check-in exists, False otherwise, with detailed logging.
    """
    operation = "check_existing_checkin"
    try:
        url = f"{HR_API_ENDPOINT}&user_id={user_id}&date={date}"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        has_checkin = bool(data.get("data") and data["data"].get("in_date") == date and data["data"].get("in_time"))
        log_to_file(
            API_CALL_LOGS_FILE,
            operation,
            "info",
            f"Checked existing check-in for user_id {user_id} on {date}: {'Found' if has_checkin else 'Not found'}.",
            {"user_id": user_id, "date": date, "has_checkin": has_checkin, "response": data}
        )
        return has_checkin
    except requests.exceptions.Timeout:
        log_to_file(
            API_CALL_LOGS_FILE,
            operation,
            "error",
            f"Timeout checking existing check-in for user_id {user_id} on {date}",
            {"user_id": user_id, "date": date, "error": "Request timeout"}
        )
        return False
    except requests.exceptions.HTTPError as http_err:
        try:
            error_data = http_err.response.json() if http_err.response else {"error": str(http_err)}
        except ValueError:
            error_data = {"error": "Non-JSON response", "raw": http_err.response.text if http_err.response else str(http_err)}
        status_code = http_err.response.status_code if http_err.response else None
        log_to_file(
            API_CALL_LOGS_FILE,
            operation,
            "error",
            f"HTTP error checking existing check-in for user_id {user_id} on {date}: {str(http_err)}",
            {"user_id": user_id, "date": date, "status_code": status_code, "response": error_data}
        )
        return False
    except Exception as e:
        log_to_file(
            API_CALL_LOGS_FILE,
            operation,
            "error",
            f"Error checking existing check-in for user_id {user_id} on {date}: {str(e)}",
            {"user_id": user_id, "date": date, "error": str(e)}
        )
        return False

@backoff.on_exception(
    backoff.expo,
    requests.exceptions.RequestException,
    max_tries=MAX_RETRY_ATTEMPTS,
    max_time=60
)
def make_api_call(endpoint, payload, operation, user_id, timestamp, punch_type):
    """
    Makes an API call with retry logic and returns the response.
    Returns response and delay (from Retry-After header or default).
    """
    try:
        response = requests.post(endpoint, json=payload, timeout=30)
        response.raise_for_status()
        retry_after = response.headers.get("Retry-After")
        delay = float(retry_after) if retry_after and retry_after.isdigit() else DEFAULT_API_DELAY
        return response, delay
    except requests.exceptions.HTTPError as http_err:
        retry_after = http_err.response.headers.get("Retry-After") if http_err.response else None
        delay = float(retry_after) if retry_after and retry_after.isdigit() else DEFAULT_API_DELAY
        raise requests.exceptions.HTTPError(f"{http_err}", response=http_err.response) from None
    except requests.exceptions.Timeout:
        print(f"⚠️ Request timeout for {operation} (user_id: {user_id})")
        raise
    except requests.exceptions.ConnectionError:
        print(f"⚠️ Connection error for {operation} (user_id: {user_id})")
        raise

def process_attendance_api_calls(date_str):
    """
    Reads records from filtered_attendance_by_date.json, processes check-in (punch: 0) first,
    then check-out (punch: 1), and logs API responses to api_call_logs.json.
    Returns counts of successful and failed API calls.
    """
    operation = "process_attendance_api_calls"
    log_to_file(
        API_CALL_LOGS_FILE,
        operation,
        "info",
        f"Starting attendance API call processing for {date_str}.",
        {"date": date_str}
    )
    success_count = 0
    failure_count = 0

    try:
        if not os.path.exists(FILTERED_ATTENDANCE_FILE):
            log_to_file(
                API_CALL_LOGS_FILE,
                operation,
                "warning",
                f"No file found at {FILTERED_ATTENDANCE_FILE} for {date_str}.",
                {"date": date_str}
            )
            return success_count, failure_count

        try:
            with open(FILTERED_ATTENDANCE_FILE, "r") as f:
                records = json.load(f)
        except json.JSONDecodeError as e:
            log_to_file(
                API_CALL_LOGS_FILE,
                operation,
                "error",
                f"Invalid JSON in {FILTERED_ATTENDANCE_FILE} for {date_str}: {str(e)}",
                {"date": date_str, "error": str(e)}
            )
            return success_count, failure_count
        except PermissionError as e:
            log_to_file(
                API_CALL_LOGS_FILE,
                operation,
                "error",
                f"Permission denied reading {FILTERED_ATTENDANCE_FILE} for {date_str}: {str(e)}",
                {"date": date_str, "error": str(e)}
            )
            return success_count, failure_count

        if not records:
            log_to_file(
                API_CALL_LOGS_FILE,
                operation,
                "warning",
                f"No records found in {FILTERED_ATTENDANCE_FILE} for {date_str}.",
                {"date": date_str}
            )
            return success_count, failure_count

        check_in_records = [r for r in records if r["punch"] == 0]
        check_out_records = [r for r in records if r["punch"] == 1]

        log_to_file(
            API_CALL_LOGS_FILE,
            operation,
            "info",
            f"Processing {len(check_in_records)} check-in records for {date_str}.",
            {"date": date_str, "check_in_count": len(check_in_records)}
        )
        log_entries = []
        processed_check_ins = set()
        processed_check_outs = set()

        for record in check_in_records:
            try:
                user_id = int(record["user_id"])
                timestamp = record["timestamp"]
                timestamp_dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                date = timestamp_dt.strftime("%Y-%m-%d")
                formatted_time = timestamp.replace(" ", "T") + ".000000Z"

                check_in_key = (user_id, date)
                if check_in_key in processed_check_ins:
                    log_to_file(
                        API_CALL_LOGS_FILE,
                        operation,
                        "warning",
                        f"Skipping duplicate check-in for user_id {user_id} on {date}.",
                        {"user_id": user_id, "date": date}
                    )
                    continue

                processed_check_ins.add(check_in_key)
                payload = {
                    "user_id": user_id,
                    "date": date,
                    "in_time": formatted_time,
                    "auth_middleware": AUTH_MIDDLEWARE,
                    "note": f"Check-in via ZKTeco device ({record['method']})",
                    "ip_data": {"ip": DEVICE_IP}
                }
                endpoint = CHECK_IN_ENDPOINT

                response, delay = make_api_call(endpoint, payload, "check-in", user_id, timestamp, "check-in")
                response_data = response.json()

                log_entry = {
                    "operation": "check-in",
                    "user_id": user_id,
                    "timestamp": timestamp,
                    "punch": "check-in",
                    "endpoint": endpoint,
                    "request_payload": payload,
                    "response_status": response.status_code,
                    "response_data": response_data,
                    "logged_at": datetime.now(pytz.timezone(TIMEZONE)).isoformat()
                }
                log_entries.append(log_entry)
                log_to_file(
                    API_CALL_LOGS_FILE,
                    "check-in",
                    "success",
                    f"API call for user_id {user_id} (check-in) succeeded: {response_data.get('message', 'Success')}",
                    {"user_id": user_id, "date": date, "response": response_data}
                )
                success_count += 1
                time.sleep(delay)

            except requests.exceptions.HTTPError as http_err:
                try:
                    response_data = http_err.response.json() if http_err.response else {"error": str(http_err)}
                    status_code = http_err.response.status_code if http_err.response else None
                except ValueError:
                    response_data = {"error": "Non-JSON response", "raw": http_err.response.text if http_err.response else str(http_err)}
                    status_code = http_err.response.status_code if http_err.response else None

                error_message = response_data.get("message", "").lower() if isinstance(response_data.get("message", ""), str) else ""
                # --- 422 error handling for check-in ---
                if status_code == 422:
                    if "already marked" in error_message or "already exists" in error_message:
                        log_to_file(
                            API_CALL_LOGS_FILE,
                            "check-in",
                            "warning",
                            f"Check-in already exists for user_id {user_id} on {date}. Skipping.",
                            {"user_id": user_id, "date": date, "response": response_data}
                        )
                        processed_check_ins.add(check_in_key)
                        continue
                    elif "no working shift assigned" in error_message:
                        log_to_file(
                            API_CALL_LOGS_FILE,
                            "check-in",
                            "error",
                            f"No working shift assigned for user_id {user_id} on {date}. Skipping.",
                            {"user_id": user_id, "date": date, "response": response_data}
                        )
                        failure_count += 1
                        processed_check_ins.add(check_in_key)
                        time.sleep(1.0)
                        continue
                    elif "validation" in error_message or "invalid" in error_message:
                        log_to_file(
                            API_CALL_LOGS_FILE,
                            "check-in",
                            "error",
                            f"Validation error for user_id {user_id} on {date}: {response_data.get('message')}",
                            {"user_id": user_id, "date": date, "response": response_data}
                        )
                        failure_count += 1
                        time.sleep(1.0)
                        continue
                    else:
                        # Other 422 errors
                        log_to_file(
                            API_CALL_LOGS_FILE,
                            "check-in",
                            "error",
                            f"422 error for user_id {user_id} on {date}: {response_data.get('message', str(http_err))}",
                            {"user_id": user_id, "date": date, "response": response_data}
                        )
                        failure_count += 1
                        time.sleep(1.0)
                        continue

                retry_after = http_err.response.headers.get("Retry-After") if http_err.response else None
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 1.0

                log_entry = {
                    "operation": "check-in",
                    "user_id": user_id,
                    "timestamp": timestamp,
                    "punch": "check-in",
                    "endpoint": endpoint,
                    "request_payload": payload,
                    "response_status": status_code,
                    "response_data": response_data,
                    "logged_at": datetime.now(pytz.timezone(TIMEZONE)).isoformat()
                }
                log_entries.append(log_entry)
                log_to_file(
                    API_CALL_LOGS_FILE,
                    "check-in",
                    "error",
                    f"API call for user_id {user_id} (check-in) failed: {response_data.get('message', str(http_err))}",
                    {"user_id": user_id, "date": date, "response": response_data}
                )
                failure_count += 1
                time.sleep(delay)

            except ValueError as ve:
                log_entry = {
                    "operation": "check-in",
                    "user_id": user_id,
                    "timestamp": timestamp,
                    "punch": "check-in",
                    "endpoint": endpoint,
                    "request_payload": payload if 'payload' in locals() else None,
                    "response_status": None,
                    "response_data": {"error": f"Invalid data: {str(ve)}"},
                    "logged_at": datetime.now(pytz.timezone(TIMEZONE)).isoformat()
                }
                log_entries.append(log_entry)
                log_to_file(
                    API_CALL_LOGS_FILE,
                    "check-in",
                    "error",
                    f"Invalid data for user_id {user_id} (check-in): {str(ve)}",
                    {"user_id": user_id, "date": date, "error": str(ve)}
                )
                failure_count += 1
                time.sleep(1.0)

            except Exception as e:
                log_entry = {
                    "operation": "check-in",
                    "user_id": user_id,
                    "timestamp": timestamp,
                    "punch": "check-in",
                    "endpoint": endpoint,
                    "request_payload": payload if 'payload' in locals() else None,
                    "response_status": None,
                    "response_data": {"error": str(e)},
                    "logged_at": datetime.now(pytz.timezone(TIMEZONE)).isoformat()
                }
                log_entries.append(log_entry)
                log_to_file(
                    API_CALL_LOGS_FILE,
                    "check-in",
                    "error",
                    f"Error during API call for user_id {user_id} (check-in): {str(e)}",
                    {"user_id": user_id, "date": date, "error": str(e)}
                )
                failure_count += 1
                time.sleep(1.0)

        log_to_file(
            API_CALL_LOGS_FILE,
            operation,
            "info",
            f"Processing {len(check_out_records)} check-out records for {date_str}.",
            {"date": date_str, "check_out_count": len(check_out_records)}
        )
        for record in check_out_records:
            try:
                user_id = int(record["user_id"])
                timestamp = record["timestamp"]
                timestamp_dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                date = timestamp_dt.strftime("%Y-%m-%d")
                formatted_time = timestamp.replace(" ", "T") + ".000000Z"

                check_out_key = (user_id, date)
                if check_out_key in processed_check_outs:
                    log_to_file(
                        API_CALL_LOGS_FILE,
                        operation,
                        "warning",
                        f"Skipping duplicate check-out for user_id {user_id} on {date}.",
                        {"user_id": user_id, "date": date}
                    )
                    continue
                processed_check_outs.add(check_out_key)

                payload = {
                    "user_id": user_id,
                    "date": date,
                    "out_time": formatted_time,
                    "auth_middleware": AUTH_MIDDLEWARE,
                    "note": f"Check-out via ZKTeco device ({record['method']})",
                    "ip_data": {"ip": DEVICE_IP}
                }
                endpoint = CHECK_OUT_ENDPOINT

                response, delay = make_api_call(endpoint, payload, "check-out", user_id, timestamp, "check-out")
                response_data = response.json()

                log_entry = {
                    "operation": "check-out",
                    "user_id": user_id,
                    "timestamp": timestamp,
                    "punch": "check-out",
                    "endpoint": endpoint,
                    "request_payload": payload,
                    "response_status": response.status_code,
                    "response_data": response_data,
                    "logged_at": datetime.now(pytz.timezone(TIMEZONE)).isoformat()
                }
                log_entries.append(log_entry)
                log_to_file(
                    API_CALL_LOGS_FILE,
                    "check-out",
                    "success",
                    f"API call for user_id {user_id} (check-out) succeeded: {response_data.get('message', 'Success')}",
                    {"user_id": user_id, "date": date, "response": response_data}
                )
                success_count += 1
                time.sleep(delay)

            except requests.exceptions.HTTPError as http_err:
                try:
                    response_data = http_err.response.json() if http_err.response else {"error": str(http_err)}
                    status_code = http_err.response.status_code if http_err.response else None
                except ValueError:
                    response_data = {"error": "Non-JSON response", "raw": http_err.response.text if http_err.response else str(http_err)}
                    status_code = http_err.response.status_code if http_err.response else None

                error_message = response_data.get("message", "").lower() if isinstance(response_data.get("message", ""), str) else ""
                # --- 422 error handling for check-out ---
                if status_code == 422:
                    if "already punched out" in error_message or "already exists" in error_message:
                        log_to_file(
                            API_CALL_LOGS_FILE,
                            "check-out",
                            "warning",
                            f"Check-out already exists for user_id {user_id} on {date}. Skipping.",
                            {"user_id": user_id, "date": date, "response": response_data}
                        )
                        processed_check_outs.add(check_out_key)
                        continue
                    elif "validation" in error_message or "invalid" in error_message:
                        log_to_file(
                            API_CALL_LOGS_FILE,
                            "check-out",
                            "error",
                            f"Validation error for user_id {user_id} on {date}: {response_data.get('message')}",
                            {"user_id": user_id, "date": date, "response": response_data}
                        )
                        failure_count += 1
                        time.sleep(1.0)
                        continue
                    else:
                        # Other 422 errors
                        log_to_file(
                            API_CALL_LOGS_FILE,
                            "check-out",
                            "error",
                            f"422 error for user_id {user_id} on {date}: {response_data.get('message', str(http_err))}",
                            {"user_id": user_id, "date": date, "response": response_data}
                        )
                        failure_count += 1
                        time.sleep(1.0)
                        continue
                if status_code == 404 and "no check-in found" in error_message:
                    log_to_file(
                        API_CALL_LOGS_FILE,
                        "check-out",
                        "error",
                        f"No check-in found for user_id {user_id} on {date}. Skipping.",
                        {"user_id": user_id, "date": date, "response": response_data}
                    )
                    failure_count += 1
                    processed_check_outs.add(check_out_key)
                    time.sleep(1.0)
                    continue

                retry_after = http_err.response.headers.get("Retry-After") if http_err.response else None
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 1.0

                log_entry = {
                    "operation": "check-out",
                    "user_id": user_id,
                    "timestamp": timestamp,
                    "punch": "check-out",
                    "endpoint": endpoint,
                    "request_payload": payload,
                    "response_status": status_code,
                    "response_data": response_data,
                    "logged_at": datetime.now(pytz.timezone(TIMEZONE)).isoformat()
                }
                log_entries.append(log_entry)
                log_to_file(
                    API_CALL_LOGS_FILE,
                    "check-out",
                    "error",
                    f"API call for user_id {user_id} (check-out) failed: {response_data.get('message', str(http_err))}",
                    {"user_id": user_id, "date": date, "response": response_data}
                )
                failure_count += 1
                time.sleep(delay)

            except ValueError as ve:
                log_entry = {
                    "operation": "check-out",
                    "user_id": user_id,
                    "timestamp": timestamp,
                    "punch": "check-out",
                    "endpoint": endpoint,
                    "request_payload": payload if 'payload' in locals() else None,
                    "response_status": None,
                    "response_data": {"error": f"Invalid data: {str(ve)}"},
                    "logged_at": datetime.now(pytz.timezone(TIMEZONE)).isoformat()
                }
                log_entries.append(log_entry)
                log_to_file(
                    API_CALL_LOGS_FILE,
                    "check-out",
                    "error",
                    f"Invalid data for user_id {user_id} (check-out): {str(ve)}",
                    {"user_id": user_id, "date": date, "error": str(ve)}
                )
                failure_count += 1
                time.sleep(1.0)

            except Exception as e:
                log_entry = {
                    "operation": "check-out",
                    "user_id": user_id,
                    "timestamp": timestamp,
                    "punch": "check-out",
                    "endpoint": endpoint,
                    "request_payload": payload if 'payload' in locals() else None,
                    "response_status": None,
                    "response_data": {"error": str(e)},
                    "logged_at": datetime.now(pytz.timezone(TIMEZONE)).isoformat()
                }
                log_entries.append(log_entry)
                log_to_file(
                    API_CALL_LOGS_FILE,
                    "check-out",
                    "error",
                    f"Error during API call for user_id {user_id} (check-out): {str(e)}",
                    {"user_id": user_id, "date": date, "error": str(e)}
                )
                failure_count += 1
                time.sleep(1.0)

        append_to_json_file(API_CALL_LOGS_FILE, log_entries)
        status = "success" if success_count > 0 else "failed"
        update_processed_dates(date_str, status, success_count, failure_count)
        log_to_file(
            API_CALL_LOGS_FILE,
            operation,
            "success",
            f"{len(log_entries)} API call logs saved to {API_CALL_LOGS_FILE} for {date_str}. Success: {success_count}, Failed: {failure_count}.",
            {"date": date_str, "log_count": len(log_entries), "success_count": success_count, "failure_count": failure_count}
        )
        log_to_file(
            API_CALL_LOGS_FILE,
            operation,
            "info",
            f"Completed attendance API call processing for {date_str}.",
            {"date": date_str}
        )

        return success_count, failure_count

    except Exception as e:
        log_to_file(
            API_CALL_LOGS_FILE,
            operation,
            "error",
            f"Error processing attendance API calls for {date_str}: {str(e)}",
            {"date": date_str, "error": str(e)}
        )
        update_processed_dates(date_str, "failed", success_count, failure_count)
        return success_count, failure_count

if __name__ == "__main__":
    # Validate CSV file exists before proceeding
    if not validate_csv_file():
        print("❌ Cannot proceed without valid CSV file. Exiting.")
        sys.exit(1)
    
    print(f"✅ Configuration loaded:")
    print(f"   - Device IP: {DEVICE_IP}")
    print(f"   - CSV File: {CSV_FILE}")
    print(f"   - Timezone: {TIMEZONE}")
    print(f"   - API Delay: {DEFAULT_API_DELAY}s")
    print(f"   - Max Retry Attempts: {MAX_RETRY_ATTEMPTS}")
    
    # Truncate specified files
    files_to_truncate = ["last_attendance_from_device.json", "last_attendance_from_api.json", "filtered_attendance_by_date.json"]
    for file_path in files_to_truncate:
        try:
            with open(file_path, "w") as f:
                json.dump({}, f, indent=4)
            print(f"✅ Successfully truncated {file_path}")
        except Exception as e:
            print(f"❌ Error truncating {file_path}: {str(e)}")

    # Initial fetch of attendance data
    print("\n🔄 Fetching attendance data...")
    all_records = fetch_all_attendance_from_csv()
    if all_records is None:
        print("❌ Failed to fetch attendance records from CSV. Exiting.")
        sys.exit(1)
    
    last_record = fetch_last_attendance_from_csv()
    if last_record is None:
        print("❌ Failed to fetch last attendance record. Exiting.")
        sys.exit(1)
    
    in_date = get_from_hr_api()
    if in_date is None:
        print("⚠️ Failed to fetch in_date from HR API. Will attempt to proceed with local data.")
        # Try to use a default starting date if API fails
        in_date = (datetime.now(pytz.timezone(TIMEZONE)) - timedelta(days=7)).strftime("%Y-%m-%d")
        print(f"Using default start date: {in_date}")
    
    print(f"✅ Fetched in_date from API: {in_date}")

    # Read timestamps and compare dates
    try:
        # Read device timestamp
        with open(DEVICE_OUTPUT_FILE, "r") as f:
            device_data = json.load(f)
        device_timestamp = device_data.get("timestamp")
        if not device_timestamp:
            print(f"❌ No timestamp found in {DEVICE_OUTPUT_FILE}")
            exit(1)
        device_date = datetime.strptime(device_timestamp, "%Y-%m-%d %H:%M:%S").date()
        print(f"Device timestamp: {device_timestamp} (Date: {device_date})")

        # Read API in_date
        with open(API_OUTPUT_FILE, "r") as f:
            api_data = json.load(f)
        api_in_date = api_data.get("data", {}).get("data", {}).get("in_date")
        if not api_in_date:
            print(f"❌ No in_date found in {API_OUTPUT_FILE}")
            exit(1)
        api_date = datetime.strptime(api_in_date, "%Y-%m-%d").date()
        print(f"API in_date: {api_in_date}")

        # Process all dates from api_date to device_date (inclusive)
        start_date = api_date
        end_date = device_date
        print(f"Processing attendance records from {start_date} to {end_date}.")
        total_success = 0
        total_failure = 0

        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime("%Y-%m-%d")
            if is_date_processed(date_str):
                print(f"✅ Skipping {date_str}: Already processed successfully.")
                log_to_file(
                    API_CALL_LOGS_FILE,
                    "main",
                    "info",
                    f"Skipped processing for {date_str}: Already processed successfully.",
                    {"date": date_str}
                )
                current_date += timedelta(days=1)
                continue

            print(f"Processing attendance for {date_str}")
            filter_device_attendance_by_date(date_str)
            success, failure = process_attendance_api_calls(date_str)
            total_success += success
            total_failure += failure
            current_date += timedelta(days=1)

        # Refresh device data
        print("Refreshing device data...")
        fetch_all_attendance_from_csv()
        fetch_last_attendance_from_csv()

        # Re-check dates
        with open(DEVICE_OUTPUT_FILE, "r") as f:
            device_data = json.load(f)
        device_timestamp = device_data.get("timestamp")
        device_date = datetime.strptime(device_timestamp, "%Y-%m-%d %H:%M:%S").date()

        log_to_file(
            API_CALL_LOGS_FILE,
            "main",
            "info",
            f"Completed processing. Total API calls: Success={total_success}, Failed={total_failure}. Final device date: {device_date}, API in_date: {api_date}.",
            {"total_success": total_success, "total_failure": total_failure, "device_date": str(device_date), "api_date": str(api_date)}
        )

        if device_date < api_date:
            print(f"❌ Device date ({device_date}) is still behind API in_date ({api_date}) after refresh.")
        else:
            print(f"✅ Device date ({device_date}) is up-to-date with or ahead of API in_date ({api_date}).")

    except json.JSONDecodeError as e:
        print(f"❌ JSON error in device or API file: {str(e)}")
        log_to_file(API_CALL_LOGS_FILE, "main", "error", f"JSON error: {str(e)}", {"error": str(e)})
    except FileNotFoundError as e:
        print(f"❌ File not found: {str(e)}")
        log_to_file(API_CALL_LOGS_FILE, "main", "error", f"File not found: {str(e)}", {"error": str(e)})
    except Exception as e:
        print(f"❌ Error in date comparison or processing: {str(e)}")
        log_to_file(API_CALL_LOGS_FILE, "main", "error", f"Processing error: {str(e)}", {"error": str(e)})