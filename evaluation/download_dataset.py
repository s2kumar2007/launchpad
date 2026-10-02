"""Downloads the public medical-appointment no-show dataset. NOT committed to the repo.
Licence must be checked first (docs/open-items.md). Needs the Kaggle CLI + your own API credentials."""
import shutil, subprocess, sys
if not shutil.which("kaggle"):
    sys.exit("Install the Kaggle CLI (pip install kaggle), add your API token, check the dataset licence, then rerun.\nDataset: 'Medical Appointment No Shows' on kaggle.com (slug to confirm: joniarroba/noshowappointments).")
sys.exit(subprocess.call(["kaggle", "datasets", "download", "-d", "joniarroba/noshowappointments", "-p", "evaluation/data", "--unzip"]))
