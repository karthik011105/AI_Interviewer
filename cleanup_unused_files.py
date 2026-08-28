import os

files_to_delete = [
    r"e:\interview_simulator\frontend\src\components\VoiceInterface.jsx",
    r"e:\interview_simulator\frontend\src\pages\ResetPasswordPage.jsx",
    r"e:\interview_simulator\frontend\src\lib\supabaseClient.js",
    r"e:\interview_simulator\data\problems\core_bank.json"
]

def cleanup():
    print("Starting cleanup of unused/duplicate files...")
    for file_path in files_to_delete:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                print(f"[DELETED] {file_path}")
            except Exception as e:
                print(f"[ERROR] Could not delete {file_path}: {e}")
        else:
            print(f"[SKIPPED] {file_path} (does not exist)")
            
    # Check if data/problems directory is empty, and remove if it is
    problems_dir = r"e:\interview_simulator\data\problems"
    if os.path.exists(problems_dir) and not os.listdir(problems_dir):
        try:
            os.rmdir(problems_dir)
            print(f"[DELETED DIR] {problems_dir}")
        except:
            pass

if __name__ == "__main__":
    cleanup()
