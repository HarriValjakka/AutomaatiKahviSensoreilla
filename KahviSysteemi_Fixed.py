# External module imports
from threading import Thread, Lock
import time
from json import dumps
import json
import pymssql # Microsoft SQL Server -tuki

#Asennettava grove systeemi
#pip install smbus2
#curl -sL https://github.com/Seeed-Studio/grove.py/raw/master/install.sh | sudo bash -s -

import RPi.GPIO as GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(17, GPIO.IN)   # PIR-sensori input.
GPIO.setup(20, GPIO.OUT, initial=GPIO.LOW) # Rele
GPIO.setup(14, GPIO.IN) # Kytkinvirta
GPIO.setup(22, GPIO.IN)

import adc      #Groven tarjoama sensorin lukemista auttava systeemi
import i2c      #Groven tarjoama sensorin lukemista auttava systeemi
import lcdtest  #Testiksi tehty koodi, jossa oli toimiva functio joka uusio käytetään

# asennettava: pip install iothub-device-client
from azure.iot.device import IoTHubDeviceClient, Message
global adc

class KahviSysteemi:
    def __init__(self):
        #Azure jutut, lisää oma HostName ja SharedAccessKey
        cs='HostName=lisaaOma;DeviceId=KahviSysteemi;SharedAccessKey=lisaaOma'
        self.LAITE = "KahviSysteemi"
        self.cd = IoTHubDeviceClient.create_from_connection_string(cs)
        self.cd.connect()
        # luodaan saikeet saikeet-atribuuttiin
        self.saikeet = list()

        # saikeiden kaynnistys
        self.saikeet.append(Thread(target=self.nappis))
        self.saikeet.append(Thread(target=self.pirController))
        self.saikeet.append(Thread(target=self.valoAnalogController))
        self.saikeet.append(Thread(target=self.kahvinkeitinKontrolleri))
        
        #Hirveesti muuttujia globaalilla tasolla, kamala virhe mutta tehdään helposti
        self.loppu = False
        self.pirLiikeHavaittu = False
        self.valo = 0
        self.kahvinkeitinPaalla = False
        self.kahvinkeitonAsetettuAika = 10
        self.keitettyAika = 0
        self.naytollaKiire = False

        #SQL database connection, db server loytyy databasen overwiev kohdasta
        #Database kutsuja varten olevia muuttujia, pitäs varmaa piilottaa salasanat
        self.server = 'iot-server-iot.database.windows.net'
        self.database = 'db_iot_ja_sulautetut'
        self.username = 'harrivaljakka'
        self.password = 'MaanantaiTiistai321'
        self.driver= '{ODBC Driver 17 for SQL Server}'
        

        #WiringPi jutut, aka pinnit mitä käytetäään. Laitettiin tänne että helpompi vaihtaa
        self.pirPin = 17 # Pin for PIR, input for movement sensor
        self.ldrPin = 0 # Analogspotti on 0 ldr valo jutulle
        self.potenPin = 1 # analogispotti on 1 potentiometrille
        self.relePin = 20 #Pinni, joka laittaa releen paalle
        self.releOnnistumisPin = 22 #Pinni, josta rele ppalauttaa ilmoituksen päälle laitosta
        self.vipuPin = 14 #vipuu jolla käynnistää kahvinkeitin kun halutaan

    #Mainissa käynnistetään säikeet ja suljetaan ne, nollataan GPIO pinnit lopuksi
    def main(self):
        print("sovellus kaynnistyy")
        for saie in self.saikeet:
            saie.start()

        for saie in self.saikeet:
            saie.join()

        print("sovellus sammuu")

    #Näppis funktiossa luetaan todella rumasti jos käyttäjä antaa pyyntöjä
    #Pyynnöt joko sammutus tai database kutsu
    def nappis(self):
        print('nappis functio hereilla')
        while(not self.loppu):
            rivi = input()
            if rivi == "":
                self.loppu = True
            elif rivi == "all":
                pyynto = "all"
                self.azureReceiver(self,pyynto)
            elif rivi == 'sahko':
                pyynto = "sahko"
                self.azureReceiver(self,pyynto)
            elif rivi == 'valo':
                pyynto = 'valo'
                self.azureReceiver(self,pyynto)
            elif rivi == 'liike':
                pyynto = 'liike'
                self.azureReceiver(self,pyynto)

    #Pir anturin lukija, jos huomataan liikettä lähetetään azurehubiin tieto
    #Tiedossa laite, valo, ei keittoaikaa ja liike
    #Myös boolean automatisoidulle kahvinkeitolle
    def pirController(self):
        print('liike kontrolleri hereilla')
        while(not self.loppu):
            liikeMittaus = GPIO.input(17)
            if liikeMittaus == 1:
                self.pirLiikeHavaittu = True
                self.azureSender(self.valo, 0, 1)
                time.sleep(5)
                self.pirLiikeHavaittu = False

    #mitataan valon määrä 15 minuuutin välein 15 seteissä, joista otetaan keskiarvo
    #Lähetetään keskiarvo databasee
    #Jos näytöllä ei ole muuta tekemistä, päivitetään näytölle havitunvalon määrä
    def valoAnalogController(self):
        global adc
        print('valo kontrolleri hereilla')
        adc1 = adc.ADC()
        waittime = 10
        valoarray = []
        while(not self.loppu):
            oldtime = time.time()
            waitfifteen = False
            while(not waitfifteen):
                currenttime = time.time()
                if currenttime - oldtime > waittime:
                    waitfifteen = True
            while len(valoarray) < 15:
                sampleValo = adc1.read_voltage(0)
                valoarray.append(sampleValo)
                time.sleep(1)
            avaragevalo = sum(valoarray)/len(valoarray)
            avaragevalo = round(avaragevalo,2)
            self.valo = avaragevalo
            valoarray = []
            self.azureSender(self.valo, 0, 0)
            if self.kahvinkeitinPaalla == False and self.naytollaKiire == False:
                viesti= 'havaittu valo: {}'.format(self.valo)    
                self.lcdNayttoviesti(viesti)

    #alustetaan mitä luetaan kahvinkeitintä varten
    #jos potentiometrina arvo muuttuu, päivitetään sen mukaan kahvinkeitto aika ja printataan se näytölle
    def kahvinkeitinKontrolleri(self):
        global adc
        print('kahvinkeitin kontrolli hereilla')
        releKontrolli = GPIO.output(self.relePin, GPIO.LOW)
        releOnnistunut = GPIO.input(self.releOnnistumisPin)
        kaynnistysvipu = GPIO.input(self.vipuPin)
        adc2 = adc.ADC()
        x = 1
        adcread = adc2.read_voltage(self.potenPin)
        while(not self.loppu):
            if adcread != adc2.read_voltage(self.potenPin) and self.kahvinkeitinPaalla == False:
                adcread = adc2.read_voltage(self.potenPin) 
                self.kahvinkeitonAsetettuAika = adcread*0.878  #Potentiometrillä määritetään keittoaika
                viesti = 'kahvin keittoaika:  {:.2f} min'.format(self.kahvinkeitonAsetettuAika/60)
                self.lcdNayttoviesti(viesti)
                time.sleep(1)
            
            #jos ehdot täyttyy tai käyttäjää laittaa kahvinkeittimen päälle, käynnistetään keitto
            if (self.valo > 250 and self.pirLiikeHavaittu == True) or kaynnistysvipu == 1:
                print('Kahvinkeitto alkanut')
                GPIO.output(self.relePin, GPIO.HIGH)
                time.sleep(1)
                if x == 1:
                    print('rele vastasi onnistuneesti')
                    self.kahvinkeitinPaalla = True
                    kahvinkeitonAlku = time.time()
                    while(self.kahvinkeitinPaalla == True):
                        #Tehdäään looppi jossa katsotaan kauan kahvinkeitin ollut päällä
                        #Printataan kahvinkeitto aika näytölle
                        kahvinkeitonLoppu = time.time()
                        tdelta = kahvinkeitonLoppu - kahvinkeitonAlku
                        self.keitettyAika = tdelta
                        viesti = "keitetty aika: {}".format(self.keitettyAika)
                        self.lcdNayttoviesti(viesti)
                        #Jos aika täyttyy tai käyttäjä sammuttaa kahvinkeittimen, laitetaan näytölle viesti kahvinkeitto ajasta
                        #Lähetään kahvinkeiton tiedot databaseen
                        #printataan dabasesta tieto paljon käytetty kwH kahvinkeittoon
                        if (self.keitettyAika >= self.kahvinkeitonAsetettuAika) or kaynnistysvipu == 0:
                            GPIO.output(self.relePin, GPIO.LOW)
                            viesti = 'Sammuu, aika: \n {} s'.format(self.keitettyAika)
                            print(viesti)
                            self.lcdNayttoviesti( viesti)
                            self.azureSender(self.valo,self.keitettyAika,0)
                            time.sleep(5)
                            pyynto="sahko"
                            self.azureReceiver(pyynto)
                            time.sleep(5)
                            self.keitettyAika=0
                            self.kahvinkeitinPaalla = False
                        else:
                            time.sleep(1)

    #näyttö kontrolli, otetaan viesti vastaan ja koitetaan ylläpitää sitä ettei pyydetä montaa kertaa samaan aikaan
    #Käytetään LCDtest filussa olevaa setText funktiota
    def lcdNayttoviesti(self, viesti):
        self.naytollaKiire = True
        print(viesti)
        lcdtest.setText(viesti)
        self.naytollaKiire = False

    #Lähetetään viesti azurehubiin, josta viesti välitetään eteenpäin Stream Analytics Jobin kautta sql databaseen
    def azureSender(self,valo,keitettyaika,pir):
        print(valo, keitettyaika, pir)
        sanoma = Message(
            dumps({"Laite": self.LAITE,
                    "valoisuus": valo,
                    "keittoAika": keitettyaika,
                    "Pir": pir,
                    "Aika": round(time.time(), 3)}))
        self.cd.send_message(sanoma)

    #luodaan database yhteys, printataan pyynnöt suoraan tai jsondummbin kautta
    def azureReceiver(self,pyynto):
        conn = pymssql.connect(server = self.server,
        database = self.database,
        user= "{}@{}".format(self.username, self.server),
        password = self.password)
        
        with conn.cursor() as cursor:

            if pyynto == "all":
                cursor.execute("SELECT * FROM kahviprojekti")
                row = cursor.fetchone()
                #datalistoja
                laitedumb=[]
                valoisuusdumb=[]
                keittoaikadumb=[]
                pirdumb=[]
                aikadumb=[]
                
                while row is not None:
                    
                    laitedumb.append(row[0])
                    valoisuusdumb.append(row[1])
                    keittoaikadumb.append(row[2])
                    pirdumb.append(row[3])
                    aikadumb.append(row[4])
                    row = cursor.fetchone()
                i=0
                while i < len(laitedumb):
                    
                    print("Laite: "+ laitedumb[i]+",valo: " + str(valoisuusdumb[i]) +",keittoaika: "+str(keittoaikadumb[i]) + ",pir: " + str(pirdumb[i]) + ",aika: " + str(aikadumb[i]))
                    i+=1
            elif pyynto == "sahko":
                
                cursor.execute("SELECT KeittoAika FROM kahviprojekti \
                WHERE Aikaleima >= DATEADD(day, -30, getdate())")
                for row in cursor:
                    sahkoall=0
                    sahkodumb=[]
                    row = cursor.fetchone()
                    while row is not None:
                        sahkodumb.append(row[0])
                        row = cursor.fetchone()
                sahkoall = sum(sahkodumb)
                hours = sahkoall / 3600
                kwhours = hours*1.5
                viesti = "sahko past 30d={:.2f}kWh".format(kwhours)
                self.lcdNayttoviesti(viesti)
            elif pyynto == "valo":
                cursor.execute("SELECT Valoisuus FROM kahviprojekti \
                WHERE Aikaleima >= DATEADD(day, -1, getdate())")
                valoisuus=[]
                for row in cursor:
                    test = json.dumps([x for x in row], sort_keys=True)
                    myArray = json.loads(test)
                    for x in myArray:
                        valoisuus.append(x)
                valoisuus.sort()
                viesti = 'eilisen himmein: ' + valoisuus[0] + 'kirkkain: ' + valoisuus[-1]
                self.lcdNayttoviesti(viesti)
            elif pyynto == "liike":
                cursor.execute("SELECT * FROM kahviprojekti \
                WHERE Liike=1 AND Aikaleima >= DATEADD(day, -30, getdate())")
                row = cursor.fetchone()
                laitedumb=[]
                valoisuusdumb=[]
                keittoaikadumb=[]
                pirdumb=[]
                aikadumb=[]
                while row is not None:
                    laitedumb.append(row[0])
                    valoisuusdumb.append(row[1])
                    keittoaikadumb.append(row[2])
                    pirdumb.append(row[3])
                    aikadumb.append(row[4])
                    row = cursor.fetchone()
                i=0
                while i < len(laitedumb):
                    print("Laite: "+ laitedumb[i]+",valo: " + str(valoisuusdumb[i]) +",keittoaika: "+str(keittoaikadumb[i]) + ",pir: " + str(pirdumb[i]) + ",aika: " + str(aikadumb[i]))
                    i+=1

    #tekstiviesti moduli, ei implementoitu
    def textSender():
        print("Ei implementoitu, lähetä tekstiviestii käyttääjälle että kahvinkeitin sammuu")
        
#MAIN
if __name__ == '__main__':
    KahviSysteemi().main()