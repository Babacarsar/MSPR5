from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
import requests
import json
import certifi
import boto3
from botocore.exceptions import NoCredentialsError
import redis  # Ajout de Redis pour la mise en cache

# Arguments par défaut pour le DAG
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
    'start_date': datetime(2025, 3, 15),
}

# Définition du DAG
dag = DAG(
    'api_data_collection',
    default_args=default_args,
    description='Collecte quotidienne des données météo et qualité de l\'air',
    schedule_interval='0 8 * * *',  # Tous les jours à 8h00
    catchup=False,
)

# Définir les URLs des APIs
API_URLS = {
    "air_quality": "https://api.waqi.info/feed/France/?token=ae505f494f6b6038bce8986a896ae07996f8cb9a",
    "weather": "https://api.openweathermap.org/data/2.5/weather?q=paris&appid=8f991a15ce8eb3b370f488e26b90999a"
}

# Configuration MinIO - Utiliser le nom du service Docker au lieu de localhost
MINIO_ENDPOINT = "http://minio:9000"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
BUCKET_NAME = "api-data"

# Configuration Redis
REDIS_HOST = "redis"
REDIS_PORT = 6379
REDIS_DB = 0


def create_bucket_if_not_exists():
    """Vérifier si le bucket existe, sinon le créer"""
    try:
        s3_client = boto3.client(
            "s3",
            endpoint_url=MINIO_ENDPOINT,
            aws_access_key_id=MINIO_ACCESS_KEY,
            aws_secret_access_key=MINIO_SECRET_KEY,
            region_name="us-east-1"
        )

        try:
            s3_client.head_bucket(Bucket=BUCKET_NAME)
            print(f"Le bucket '{BUCKET_NAME}' existe déjà.")
        except:
            print(f"Le bucket '{BUCKET_NAME}' n'existe pas. Création en cours...")
            s3_client.create_bucket(Bucket=BUCKET_NAME)
            print(f"Bucket '{BUCKET_NAME}' créé avec succès.")

    except Exception as e:
        print(f"Erreur lors de la vérification/création du bucket: {e}")
        raise


def fetch_and_save_api_data(api_name, url):
    """Récupérer les données d'une API, les mettre en cache dans Redis et les enregistrer dans MinIO"""
    try:
        # Connexion à Redis
        redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)

        # Clé de cache unique pour cette API
        cache_key = f"{api_name}_data"

        # Vérifier si les données sont déjà en cache
        cached_data = redis_client.get(cache_key)
        if cached_data:
            print(f"Données {api_name} récupérées depuis le cache Redis.")
            data = json.loads(cached_data)
        else:
            print(f"Récupération des données depuis {api_name}...")
            response = requests.get(url, timeout=10, verify=certifi.where())
            response.raise_for_status()
            data = response.json()

            # Mettre les données en cache dans Redis (durée de validité : 1 heure)
            redis_client.setex(cache_key, timedelta(hours=1), json.dumps(data))
            print(f"Données {api_name} mises en cache dans Redis.")

        # Générer un nom de fichier basé sur l'heure actuelle
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{api_name}_{timestamp}.json"

        # Convertir les données en string JSON
        json_data = json.dumps(data, indent=4)

        # Initialiser le client MinIO
        s3_client = boto3.client(
            "s3",
            endpoint_url=MINIO_ENDPOINT,
            aws_access_key_id=MINIO_ACCESS_KEY,
            aws_secret_access_key=MINIO_SECRET_KEY,
            region_name="us-east-1"
        )

        # Envoyer le fichier JSON sur MinIO
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=filename,
            Body=json_data,
            ContentType="application/json"
        )

        print(f"Données {api_name} enregistrées sur MinIO dans {BUCKET_NAME}/{filename}")
        return f"Fichier {filename} créé avec succès"

    except requests.exceptions.RequestException as e:
        print(f"Erreur lors de la récupération des données {api_name}: {e}")
        raise
    except NoCredentialsError:
        print("Erreur: Identifiants MinIO incorrects")
        raise
    except Exception as e:
        print(f"Erreur inattendue: {e}")
        raise


# Tâche pour créer le bucket si nécessaire
create_bucket_task = PythonOperator(
    task_id='create_bucket_if_needed',
    python_callable=create_bucket_if_not_exists,
    dag=dag,
)

# Créer des tâches pour chaque API
api_tasks = []
for api_name, url in API_URLS.items():
    task = PythonOperator(
        task_id=f'fetch_{api_name}_data',
        python_callable=fetch_and_save_api_data,
        op_kwargs={'api_name': api_name, 'url': url},
        dag=dag,
    )
    api_tasks.append(task)

# Définir l'ordre des tâches
create_bucket_task >> api_tasks