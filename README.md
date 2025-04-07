To make it work first run

docker pull jlesage/firefox:latest

then 

docker-compose up --build -d

then make the port public  then get link then put it in dockerconttler.yaml in the reverse proxy base place

then docker-compose up -d --force-recreate controller then just go to the port and do it

ps everytime you reload you got to do docker ps

then docker stop id or name

then  docker-compose down

then docker-compose up --build -d

then  then make the port public  then get link then put it in dockerconttler.yaml in the reverse proxy base place

then docker-compose up -d --force-recreate controller then just go to the port and do it
