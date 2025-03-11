import boto3
import os
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator

# Configuration MinIO
MINIO_URL = "http://localhost:9001"  # Port modifié à 9001
ACCESS_KEY = "minioadmin"
SECRET_KEY = "minioadmin"
BUCKET_NAME = "test"
FILE_NAME = "test_file.txt"

# Définir les chemins des fichiers locaux
LOCAL_DIR = os.path.join(os.path.expanduser("~"), "Documents", "MSPR")  # Répertoire où le fichier sera créé
LOCAL_PATH = os.path.join(LOCAL_DIR, FILE_NAME)
DOWNLOAD_PATH = os.path.join(LOCAL_DIR, "minio_downloaded.txt")

# Fonction d'upload vers MinIO
def upload_to_minio():
    """Écrit un fichier sur MinIO."""
    print(f"Début de l'upload vers MinIO à {datetime.now()}")

    # Vérifier que le dossier existe
    os.makedirs(LOCAL_DIR, exist_ok=True)

    # Création du fichier local
    with open(LOCAL_PATH, "w") as f:
        f.write("Hello MinIO! Test avec Airflow.")

    # Connexion à MinIO
    s3_client = boto3.client(
        "s3",
        endpoint_url=MINIO_URL,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
    )

    # Upload du fichier
    try:
        s3_client.upload_file(LOCAL_PATH, BUCKET_NAME, FILE_NAME)
        print(f"✅ Fichier {FILE_NAME} uploadé sur {BUCKET_NAME}")
    except Exception as e:
        print(f"🚨 Erreur lors de l'upload : {e}")

# Fonction de lecture depuis MinIO
def read_from_minio():
    """Lit un fichier depuis MinIO."""
    print(f"Début de la lecture depuis MinIO à {datetime.now()}")

    s3_client = boto3.client(
        "s3",
        endpoint_url=MINIO_URL,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
    )

    # Vérifier que le dossier existe pour le téléchargement
    os.makedirs(LOCAL_DIR, exist_ok=True)

    # Téléchargement du fichier
    try:
        s3_client.download_file(BUCKET_NAME, FILE_NAME, DOWNLOAD_PATH)
        with open(DOWNLOAD_PATH, "r") as f:
            content = f.read()
        print(f"📄 Contenu du fichier téléchargé : {content}")
        return content
    except Exception as e:
        print(f"🚨 Erreur lors du téléchargement : {e}")
        return None

# Fonction pour lister les objets dans le bucket
def list_objects_in_bucket():
    """Liste tous les objets dans un bucket."""
    s3_client = boto3.client(
        "s3",
        endpoint_url=MINIO_URL,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
    )

    try:
        response = s3_client.list_objects_v2(Bucket=BUCKET_NAME)

        if response.get('Contents'):
            print(f"Objets dans le bucket {BUCKET_NAME}:")
            for obj in response['Contents']:
                print(f" - {obj['Key']} ({obj['Size']} bytes)")
        else:
            print(f"🚨 Le bucket {BUCKET_NAME} est vide")
    except Exception as e:
        print(f"🚨 Erreur lors de la liste des objets : {e}")

# Définition du DAG
default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "start_date": datetime(2025, 3, 11),
    "retries": 1,
    "retry_delay": timedelta(minutes=1),
}

dag = DAG(
    "minio_dag",
    default_args=default_args,
    description="Un DAG pour écrire et lire depuis MinIO",
    schedule_interval=None,
    catchup=False,
)

# Tâches
upload_task = PythonOperator(
    task_id="upload_to_minio",
    python_callable=upload_to_minio,
    dag=dag,
)

read_task = PythonOperator(
    task_id="read_from_minio",
    python_callable=read_from_minio,
    dag=dag,
)

list_task = PythonOperator(
    task_id="list_objects_in_bucket",
    python_callable=list_objects_in_bucket,
    dag=dag,
)

# Dépendances
upload_task >> list_task >> read_task
