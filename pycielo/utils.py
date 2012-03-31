import re
import datetime
from cStringIO import StringIO
from decimal import Decimal
from xml.dom.minidom import Document
from xml.dom import Element

from lxml import etree
from lxml.builder import E
import pycurl


BANDEIRAS = (
    ('visa', 'Visa'),
    ('mastercard', 'Mastercard'),
    ('diners', 'Diners'),
    ('discover', 'Discover'),
    ('elo', 'Elo'),
    )


class Status(object):
    CRIADA = 0
    EM_ANDAMENTO = 1
    AUTENTICADA = 2
    NAO_AUTENTICADA = 3
    AUTORIZADA = 4
    NAO_AUTORIZADA = 5
    CAPTURADA = 6
    NAO_CAPTURADA = 8
    CANCELADA = 9
    EM_AUTENTICACAO = 10

    def __init__(self, st):
        self._status = int(st)

    def __repr__(self):
        return '<Status: %s>' % self._status


class RequisicaoTransacao(object):

    def __init__(self, cielo, _id):
        self.__cielo = cielo
        self.__id = _id

    def enviar(self):
        e = E("requisicao-transacao",
            {
                'id': str(self.__id),
                'versao': self.__cielo.VERSION,
            },
            self.__cielo.dadosEc(),
            self.__cielo.dadosPedido(),
            self.__cielo.formaPagamento(),
            self.__cielo.urlRetorno(),
            self.__cielo.autorizar(),
            self.__cielo.capturar(),
        )
        doc = etree.tostring(e, pretty_print=True)

        return self.__cielo.send(doc)


class Transacao(object):

    tid = None
    url = None
    valor = None
    __status = None

    def __init__(self, root):
        self.__root = root

        self.tid = self.__root.xpath('/transacao/tid')[0].text
        r = self.__root.xpath('/transacao/url-autenticacao')
        if len(r) > 0:
            self.url = r[0].text
        r = self.__root.xpath('/transacao/status')
        if len(r) > 0:
            self.status = r[0].text

        r = self.__root.xpath('/transacao/captura/valor')
        if len(r) > 0:
            self.valor = Decimal(r[0].text) / Decimal('100')

    def pprint(self):
        print etree.tostring(self.__root, pretty_print=True)

    @property
    def status(self):
        return self.__status

    @status.setter
    def status(self, value):
        self.__status = Status(value)


class Cielo(object):

    VERSION = '1.1.1'
    URL = "https://qasecommerce.cielo.com.br/servicos/ecommwsec.do"

    def __init__(self, storeid, storekey):
        self.__store_id = storeid
        self.__sotre_key = storekey

    def setPedido(self, pedido, valor):
        self.__pedido = str(pedido)
        self.__valor = str(int(valor * 100))

    def setRetorno(self, retorno):
        self.__retorno = retorno

    def setFormaPag(self, bandeira, produto, parcelas):
        #bandeira:
        # visa, mastercard, diners, discover ou elo
        if not filter(lambda y: y[0] == bandeira, BANDEIRAS):
            raise ValueError(bandeira)

        #produto:
        # 1 (Credito a Vista),
        # 2 (Parcelado loja),
        # 3 (Parcelado administradora)

        self.__bandeira = bandeira
        self.__produto = produto
        self.__parcelas = str(parcelas)

    def setCapturar(self, capturar):
        self.__capturar = capturar

    def getUri(self):
        return self.URL

    def dadosEc(self):
        e = E("dados-ec",
            E.numero(self.__store_id),
            E.chave(self.__sotre_key),
            )
        return e

    def dadosPedido(self):
        e = E("dados-pedido",
                E.numero(self.__pedido),
                E.valor(self.__valor),
                E.moeda('986'),
                E("data-hora", datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")),
                E.idioma('PT')
            )
        return e

    def urlRetorno(self):
        e = E('url-retorno',
            self.__retorno
            )
        return e

    def autorizar(self):
        e = E.autorizar('2')
        return e

    def capturar(self):
        e = E.capturar(self.__capturar)
        return e

    def formaPagamento(self):
        e = E("forma-pagamento",
            E.bandeira(self.__bandeira),
            E.produto(self.__produto),
            E.parcelas(self.__parcelas),
            )
        return e

    def requestTid(self, id):

        doc = Document()

        # Create the <requisicao-tid> base element
        tid = doc.createElement("requisicao-tid")
        tid.setAttribute("id", str(id))
        tid.setAttribute("versao", self.VERSION)
        tid.appendChild(self.dadosEc(doc))
        tid.appendChild(self.formaPagamento(doc))
        #tid.appendChild(self.dadosPedido(doc))
        doc.appendChild(tid)

        return self.send(doc)

    def requisicaoTransacao(self, _id):
        return RequisicaoTransacao(self, _id)

    def requestConsulta(self, id, tid):

        e = E("requisicao-consulta", {'id': str(id), 'versao': self.VERSION},
            E('tid', tid),
            self.dadosEc(),
        )
        doc = etree.tostring(e, pretty_print=True)

        return self.send(doc)

    def requestAutorizacaoPortador(self, id, tid):

        doc = Document()

        # Create the <requisicao-autorizacao-portador> base element
        rap = doc.createElement("requisicao-autorizacao-portador")
        rap.setAttribute("id", str(id))
        rap.setAttribute("versao", self.VERSION)

        tid = doc.createElement('tid')
        tid.appendChild(doc.createTextNode(tid))
        rap.appendChild(tid)
        rap.appendChild(self.dadosEc(doc))
        #rap.appendChild(self.dadosCartao(doc))
        rap.appendChild(self.dadosPedido(doc))
        rap.appendChild(self.formaPagamento(doc))
        rap.appendChild(self.urlRetorno(doc))
        rap.appendChild(self.autorizar(doc))
        rap.appendChild(self.capturar(doc))
        doc.appendChild(rap)

        return self.send(doc)

    def requestAutorizacaoTid(self, id, tid):

        doc = Document()

        # Create the <requisicao-autorizacao-tid> base element
        rat = doc.createElement("requisicao-autorizacao-tid")
        rat.setAttribute("id", str(id))
        rat.setAttribute("versao", self.VERSION)

        tidn = doc.createElement('tid')
        tidn.appendChild(doc.createTextNode(tid))
        rat.appendChild(tidn)
        rat.appendChild(self.dadosEc(doc))
        doc.appendChild(rat)

        return self.send(doc)

    def send(self, xml):
        """
        returns Transacao
        """
        t = StringIO()

        post = "mensagem=%s" % xml
        c = pycurl.Curl()
        c.setopt(pycurl.URL, str(self.getUri()))
        c.setopt(pycurl.FOLLOWLOCATION, 1)
        c.setopt(pycurl.MAXREDIRS, 10)
        c.setopt(pycurl.CONNECTTIMEOUT, 10)
        c.setopt(pycurl.POST, 1)
        # Default SSL version didn't work, verify why
        c.setopt(pycurl.SSLVERSION, pycurl.SSLVERSION_SSLv3)
        c.setopt(pycurl.HTTPHEADER, [
            "Content-type: application/x-www-form-urlencoded",
            ])
        c.setopt(pycurl.POSTFIELDS, post)
        c.setopt(pycurl.TIMEOUT, 10)
        c.setopt(pycurl.WRITEFUNCTION, t.write)

        c.perform()
        c.close()

        t.seek(0)
        # I dont want to use namespaces! It is boring
        root = etree.fromstring(re.sub(r' xmlns="(.+?)"', '', t.getvalue()))
        if root.tag == 'erro':
            dic = {
                'errno': root.xpath('//codigo')[0].text,
                'errmsg': root.xpath('//mensagem')[0].text
            }
            raise ValueError(dic)
        transacao = Transacao(root)
        return transacao
