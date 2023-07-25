import os

def rename_files(directory, prefix):
    # Ensure the directory exists
    if not os.path.exists(directory):
        print(f"Directory {directory} does not exist.")
        return

    # Iterate over every file in the directory
    for filename in os.listdir(directory):
        # Construct the new filename
        new_filename = prefix + "_" + filename

        # Create the full file paths
        old_file_path = os.path.join(directory, filename)
        new_file_path = os.path.join(directory, new_filename)

        # Rename the file
        os.rename(old_file_path, new_file_path)

rename_files("/home/titanxp/Downloads/Training Data/Cartoon/JonnyQuest", prefix="jonnyquest")
