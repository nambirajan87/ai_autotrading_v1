"""
OI Pulse Live / Current Row-1 Strength Extractor
------------------------------------------------
Connects to an open Chrome browser on port 9222 and extracts:
- Row Number (Row 1)
- Date
- Time
- LTP
- Strength (from <span class="strength-pill__num">)
"""

from selenium import webdriver
from selenium.webdriver.common.by import By

def get_oipulse_row1(driver=None, close_driver=False):
    created_driver = False
    if driver is None:
        options = webdriver.ChromeOptions()
        options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
        driver = webdriver.Chrome(options=options)
        created_driver = True

    try:
        # Find the row where th.line-numbers == '1'
        row1 = None
        rows = driver.find_elements(By.CSS_SELECTOR, "tbody tr")
        for r in rows:
            line_th = r.find_elements(By.CSS_SELECTOR, "th.line-numbers")
            if line_th and line_th[0].text.strip() == "1":
                row1 = r
                break
        
        if not row1 and rows:
            row1 = rows[0]

        if not row1:
            raise RuntimeError("No rows found in OI Pulse table.")

        # Extract values
        line_num = row1.find_element(By.CSS_SELECTOR, "th.line-numbers").text.strip()
        tds = row1.find_elements(By.CSS_SELECTOR, "td")
        
        date_val = tds[0].text.strip() if len(tds) > 0 else ""
        time_val = tds[1].text.strip() if len(tds) > 1 else ""
        ltp_val = tds[2].text.strip().replace(",", "") if len(tds) > 2 else ""

        # Strength pill
        pill = row1.find_element(By.CSS_SELECTOR, ".strength-pill__num")
        # Replace unicode minus sign \u2212 with ASCII hyphen -
        strength_val = pill.text.strip().replace("\u2212", "-")

        result = {
            "Row": line_num,
            "Date": date_val,
            "Time": time_val,
            "LTP": ltp_val,
            "Strength": strength_val
        }
        return result

    finally:
        if created_driver and close_driver:
            # We don't quit the browser since it's an attached user session
            pass

if __name__ == "__main__":
    data = get_oipulse_row1()
    print("----------------------------------------")
    print("OI PULSE ROW 1 EXTRACTED DATA:")
    print("----------------------------------------")
    for k, v in data.items():
        print(f"  {k:10}: {v}")
    print("----------------------------------------")
