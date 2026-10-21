
def find_value(LTP, current_day, OIStrength):
    # Step 1: Calculate the 50-point bounds
    previous_bound = ((LTP - 1) // 50) * 50 + 1
    next_bound = previous_bound + 49

    # Step 2: Define weekday adjustments
    adjustments = {
        "Wednesday": 350,
        "Thursday": 250,
        "Friday": 150,
        "Monday": 0,
        "Tuesday": -100
    }

    # Step 3: Validate current day
    current_day = current_day.strip().capitalize()

    if current_day not in adjustments:
        print("Invalid day. Enter Monday to Friday.")
        return None, None

    # Step 4: Determine optionTypeResult
    if OIStrength > 50:
        optionTypeResult = "CE"
    elif OIStrength < -50:
        optionTypeResult = "PE"
    else:
        optionTypeResult = "No Signal"

    # Step 5: Calculate strikePriceResult based on signal and weekday
    adjustment = adjustments[current_day]

    if optionTypeResult == "CE":
        strikePriceResult = previous_bound + adjustment - 1

    elif optionTypeResult == "PE":
        strikePriceResult = previous_bound - adjustment - 1

    else:
        strikePriceResult = None

    # Step 6: Print results
    print("strikePriceResult        :", strikePriceResult)
    print("optionTypeResult :", optionTypeResult)

    return strikePriceResult, optionTypeResult


# Dynamic user inputs
LTP = int(input("Enter LTP: "))
current_day = input("Enter Current Day: ")
OIStrength = int(input("Enter OIStrength: "))

find_value(LTP, current_day, OIStrength)
