import streamlit as st
import boto3
import uuid
from datetime import datetime
import time
import os
from boto3.dynamodb.conditions import Key
from os.path import join, dirname
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む
load_dotenv(verbose=True)

# AWSセッションとDynamoDBリソースの初期化
def initialize_aws():
    # AWSの認証情報とリージョンを取得
    aws_access_key_id = os.environ.get("AWS_ACCESS_KEY")
    aws_secret_access_key = os.environ.get("AWS_SECRET_KEY")
    region_name = os.environ.get("AWS_REGION")
    dynamodb_endpoint = os.environ.get("DYNAMODB_ENDPOINT")
    
    # セッションの作成
    session = boto3.Session(
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        region_name=region_name
    )
    
    # DynamoDBリソースの作成
    # ローカルエンドポイントが設定されている場合はそれを使用
    if dynamodb_endpoint and dynamodb_endpoint.strip():
        dynamodb = session.resource('dynamodb', endpoint_url=dynamodb_endpoint)
    else:
        dynamodb = session.resource('dynamodb')
    
    return dynamodb

# DynamoDBテーブルの作成（存在しない場合）
def create_table_if_not_exists(dynamodb):
    try:
        table = dynamodb.create_table(
            TableName=os.environ.get("TABLE_NAME"),
            KeySchema=[
                {'AttributeName': 'conversation_id', 'KeyType': 'HASH'},  # パーティションキー
                {'AttributeName': 'timestamp', 'KeyType': 'RANGE'}  # ソートキー
            ],
            AttributeDefinitions=[
                {'AttributeName': 'conversation_id', 'AttributeType': 'S'},
                {'AttributeName': 'timestamp', 'AttributeType': 'N'}
            ],
            ProvisionedThroughput={'ReadCapacityUnits': 5, 'WriteCapacityUnits': 5}
        )
        # テーブルが作成されるまで待機
        table.meta.client.get_waiter('table_exists').wait(TableName=os.environ.get("TABLE_NAME"))
        st.success("DynamoDBテーブルが作成されました！")
        return table
    except Exception as e:
        # テーブルが既に存在する場合など
        print(f"テーブル作成中のエラー（既に存在する場合は無視してください）: {e}")
        return dynamodb.Table(os.environ.get("TABLE_NAME"))

# メッセージをDynamoDBに保存
def save_message(table, conversation_id, sender, message):
    timestamp = int(time.time() * 1000)  # ミリ秒タイムスタンプ
    
    response = table.put_item(
        Item={
            'conversation_id': conversation_id,
            'timestamp': timestamp,
            'sender': sender,
            'message': message,
            'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'is_deleted': False  # 削除フラグを追加
        }
    )
    return response

# DynamoDBからメッセージを削除（論理削除）
def delete_message(table, conversation_id, timestamp):
    response = table.update_item(
        Key={
            'conversation_id': conversation_id,
            'timestamp': timestamp
        },
        UpdateExpression="SET is_deleted = :val",
        ExpressionAttributeValues={
            ':val': True
        },
        ReturnValues="UPDATED_NEW"
    )
    return response

# メッセージを物理的に削除
def physically_delete_message(table, conversation_id, timestamp):
    response = table.delete_item(
        Key={
            'conversation_id': conversation_id,
            'timestamp': timestamp
        }
    )
    return response

# 会話履歴を取得（削除済みのメッセージを含むかどうかのオプション付き）
def get_conversation_history(table, conversation_id, include_deleted=False):
    response = table.query(
        KeyConditionExpression=Key('conversation_id').eq(conversation_id),
        ScanIndexForward=True  # タイムスタンプの昇順で取得
    )
    
    if include_deleted:
        return response['Items']
    else:
        # 削除されていないメッセージのみをフィルタリング
        return [item for item in response['Items'] if not item.get('is_deleted', False)]

# すべての会話IDを取得
def get_all_conversation_ids(table):
    response = table.scan(
        ProjectionExpression="conversation_id",
        Select='SPECIFIC_ATTRIBUTES'
    )
    
    # 重複を排除
    unique_ids = set()
    for item in response['Items']:
        unique_ids.add(item['conversation_id'])
    
    # 最近の会話順にソート（実際の実装ではタイムスタンプが必要）
    return list(unique_ids)

# Streamlitアプリケーションのメイン関数
def main():
    st.title("Streamlit × DynamoDB 相互入力チャットボット")
    
    # AWSとDynamoDBの初期化
    try:
        dynamodb = initialize_aws()
        table = create_table_if_not_exists(dynamodb)
    except Exception as e:
        st.error(f"DynamoDBへの接続中にエラーが発生しました: {e}")
        return
    
    # サイドバーにユーザー選択を追加
    with st.sidebar:
        st.header("ユーザー設定")
        user_type = st.radio("ユーザータイプを選択:", ["一般ユーザー", "サポート担当者"])
        
        # 既存の会話リスト（サポート担当者のみ表示）
        if user_type == "サポート担当者":
            st.header("会話リスト")
            try:
                conversation_ids = get_all_conversation_ids(table)
                if conversation_ids:
                    selected_conversation = st.selectbox(
                        "会話を選択:",
                        conversation_ids,
                        format_func=lambda x: f"会話 {x[:8]}..."
                    )
                    if st.button("この会話を開く"):
                        st.session_state.conversation_id = selected_conversation
                        st.experimental_rerun()
                else:
                    st.info("会話がまだありません")
            except Exception as e:
                st.error(f"会話リストの取得中にエラーが発生しました: {e}")
    
    # 会話IDの設定（セッションが続く限り同じIDを使用）
    if 'conversation_id' not in st.session_state:
        st.session_state.conversation_id = str(uuid.uuid4())
    
    # 現在の会話IDを表示
    st.caption(f"現在の会話ID: {st.session_state.conversation_id}")
    
    # 削除されたメッセージを表示するかどうかの設定（サポート担当者のみ）
    show_deleted = False
    if user_type == "サポート担当者":
        show_deleted = st.checkbox("削除されたメッセージを表示", value=False)
    
    # 入力フォーム - 送信者が誰かによって表示を変更
    with st.form(key="message_form", clear_on_submit=True):
        user_input = st.text_area(
            "メッセージを入力してください：", 
            height=100,
            key="user_input"
        )
        
        col1, col2 = st.columns([3, 1])
        with col1:
            # ユーザータイプに応じた送信者名
            sender = "user" if user_type == "一般ユーザー" else "support"
            sender_display = "あなた" if user_type == "一般ユーザー" else "サポート担当者"
            st.caption(f"送信者: {sender_display}")
        
        with col2:
            submit_button = st.form_submit_button("送信")
        
        if submit_button and user_input:
            try:
                # メッセージを保存
                save_message(table, st.session_state.conversation_id, sender, user_input)
                st.success("メッセージを送信しました")
            except Exception as e:
                st.error(f"メッセージの保存中にエラーが発生しました: {e}")
    
    # DynamoDBから会話履歴を取得して表示
    try:
        conversation_history = get_conversation_history(
            table, 
            st.session_state.conversation_id, 
            include_deleted=show_deleted
        )
        
        # 削除アクションの処理
        if 'delete_message' in st.session_state and st.session_state.delete_message:
            try:
                timestamp = st.session_state.delete_message
                delete_message(table, st.session_state.conversation_id, timestamp)
                st.success("メッセージを削除しました")
                # 状態をリセットしてページを更新
                st.session_state.delete_message = None
                st.experimental_rerun()
            except Exception as e:
                st.error(f"メッセージの削除中にエラーが発生しました: {e}")
        
        # 永久削除アクションの処理（サポート担当者のみ）
        if user_type == "サポート担当者" and 'permanent_delete' in st.session_state and st.session_state.permanent_delete:
            try:
                timestamp = st.session_state.permanent_delete
                physically_delete_message(table, st.session_state.conversation_id, timestamp)
                st.success("メッセージを完全に削除しました")
                # 状態をリセットしてページを更新
                st.session_state.permanent_delete = None
                st.experimental_rerun()
            except Exception as e:
                st.error(f"メッセージの完全削除中にエラーが発生しました: {e}")
        
        with st.container():
            st.subheader("会話履歴")
            for message in conversation_history:
                # メッセージが削除済みかどうかを確認
                is_deleted = message.get('is_deleted', False)
                timestamp = message['timestamp']
                
                # 現在のユーザーがメッセージの送信者と一致するか、サポート担当者かを確認
                is_own_message = (user_type == "一般ユーザー" and message['sender'] == 'user') or \
                               (user_type == "サポート担当者" and message['sender'] == 'support')
                
                # 削除されたメッセージの表示処理
                if is_deleted and show_deleted:
                    # 削除済みのメッセージは斜体で表示
                    st.markdown(f"*削除済みメッセージ ({message['date']})*")
                    if user_type == "サポート担当者":
                        # サポート担当者には元のメッセージを表示
                        st.text_area(
                            f"元のメッセージ ({message['sender']})", 
                            value=message['message'], 
                            disabled=True,
                            key=f"deleted_{timestamp}"
                        )
                        # 完全削除ボタン
                        if st.button("完全に削除", key=f"permanent_{timestamp}"):
                            st.session_state.permanent_delete = timestamp
                            st.experimental_rerun()
                    continue
                elif is_deleted and not show_deleted:
                    # 削除済みで表示しない設定の場合はスキップ
                    continue
                
                # 通常のメッセージ表示処理
                message_container = st.container()
                col1, col2 = st.columns([5, 1])
                
                with col1:
                    if message['sender'] == 'user':
                        st.text_input(
                            f"ユーザー ({message['date']})", 
                            value=message['message'], 
                            disabled=True,
                            key=f"user_{timestamp}"
                        )
                    elif message['sender'] == 'support':
                        st.text_area(
                            f"サポート担当者 ({message['date']})", 
                            value=message['message'], 
                            disabled=True,
                            key=f"support_{timestamp}"
                        )
                    elif message['sender'] == 'bot':
                        st.text_area(
                            f"自動応答ボット ({message['date']})", 
                            value=message['message'], 
                            disabled=True,
                            key=f"bot_{timestamp}"
                        )
                
                # 自分のメッセージか、サポート担当者の場合のみ削除ボタンを表示
                with col2:
                    if is_own_message or user_type == "サポート担当者":
                        if st.button("削除", key=f"delete_{timestamp}"):
                            st.session_state.delete_message = timestamp
                            st.experimental_rerun()
    except Exception as e:
        st.error(f"会話履歴の取得中にエラーが発生しました: {e}")
    
    # 新しい会話を開始するオプション
    if st.button("新しい会話を開始"):
        st.session_state.conversation_id = str(uuid.uuid4())
        st.experimental_rerun()
    
    # 自動応答ボットの有効/無効を切り替えるオプション（サポート担当者のみ）
    if user_type == "サポート担当者":
        enable_auto_response = st.checkbox("ユーザーメッセージに自動応答する", value=False)
        
        if enable_auto_response:
            st.info("自動応答が有効になっています。ユーザーからのメッセージに自動で返信されます。")

    # サイドバー下部に説明を追加
    with st.sidebar:
        st.markdown("---")
        st.markdown("### 使用方法")
        st.markdown("""
        - **一般ユーザー**: 質問や相談を入力できます
        - **サポート担当者**: ユーザーの質問に対して返答できます
        - 自分のメッセージは「削除」ボタンで削除できます
        - サポート担当者は全てのメッセージを削除できます
        - 削除されたメッセージはサポート担当者のみが閲覧可能です
        """)

if __name__ == "__main__":
    main()