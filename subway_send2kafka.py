import json
import pandas as pd
import requests
import pendulum
from datetime import datetime
from airflow import DAG
from airflow.operators.python import PythonOperator
from confluent_kafka import Producer
import os
import django
import sys

kst = pendulum.timezone("Asia/Seoul")
django_project_path = '/home/hadoop/airflow/dags/subway/mini3'
sys.path.append(django_project_path)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mini3.settings')
django.setup()
from subway.models import TrainData

dag = DAG(
    dag_id='subway_load_sample',
    description="subway_test",
    start_date=datetime(2024, 7, 1, tzinfo=kst),
    schedule_interval="*/5 0 * * *",
    catchup=False,
)

def get_info_position():
    arrive = pd.DataFrame()
    url = 'http://swopenapi.seoul.go.kr/api/subway/75796665726377333130316845727342/json/realtimePosition/0/1000/3호선'
    r = requests.get(url).json()
    data = pd.json_normalize(r, record_path=['realtimePositionList'])
    filtered_data = {key: r['errorMessage'][key] for key in ['status', 'code', 'message']}
    mes = pd.DataFrame([filtered_data])
    sample = data[['subwayNm', 'trainNo', 'statnNm', 'statnTnm', 'trainSttus', 'updnLine']]
    for col in mes.columns:
        sample[col] = mes[col].iloc[0]
    arrive = pd.concat([arrive, sample], ignore_index=True)
    return arrive

def processing_data():
    position = get_info_position()
    
    sub_Nm = {
        '1001': '1호선',
        '1002': '2호선',
        '1003': '3호선',
        '1004': '4호선',
        '1005': '5호선',
        '1006': '6호선',
        '1007': '7호선',
        '1008': '8호선',
    }
    train_stat = {
        '0': '진입',
        '1': '도착',
        '2': '출발',
        '3': '전역출발',
    }
    updnLine_Nm = {
        '0': '상행/내선',
        '1': '하행/외선'
    }

    # 데이터 전처리
    position['updnLine'] = position['updnLine'].astype(str).replace(updnLine_Nm)
    position['trainSttus'] = position['trainSttus'].astype(str).replace(train_stat)
    
    # JSON 파일로 저장
    position.to_json('/home/hadoop/workspace/realtime_position.json', force_ascii=False, orient='records')
    
    
def load_json_to_db():
    import json
    with open('/home/hadoop/workspace/realtime_position.json', 'r') as f:
        data = json.load(f)
    for item in data:
        TrainData.objects.create(
            subwayNm=item['subwayNm'],
            trainNo=item['trainNo'],
            statnNm=item['statnNm'],
            statnTnm=item['statnTnm'],
            trainSttus=item['trainSttus'],
            updnLine=item['updnLine'],
            status=item['status'],
            code=item['code'],
            message=item['message']
        )


def kafka_producer_function():
    with open('/home/hadoop/workspace/realtime_position.json', 'r') as f:
        subway_json = json.load(f)
    
    conf = {
        'bootstrap.servers': '192.168.0.209:9092,192.168.0.211:9092,192.168.0.208:9092',
        'client.id': 'airflowproducer',
    }
    producer = Producer(conf)
    message = json.dumps(subway_json)
    topic = 'subway'
    
    def save_report(err, msg):
        if err is not None:
            print(f'Message delivery failed: {err}')
        else:
            print(f'Message delivered to {msg.topic()} ][{msg.partition()}]')
    
    producer.produce(topic, message.encode('utf-8'), callback=save_report)
    producer.flush()

get_info_position_task = PythonOperator(
    task_id='get_info_position',
    python_callable=get_info_position,
    dag=dag,
)

processing_data_task = PythonOperator(
    task_id='processing_data',
    python_callable=processing_data,
    dag=dag,
)

send2DB = PythonOperator(
    task_id="send_db",
    python_callable=load_json_to_db,
    dag=dag,
)

produce_to_topic_task = PythonOperator(
    task_id="produce_to_topic",
    python_callable=kafka_producer_function,
    dag=dag,
)

get_info_position_task >> processing_data_task >> send2DB >> produce_to_topic_task