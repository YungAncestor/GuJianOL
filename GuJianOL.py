import struct, os, re

def b2q(bytes):
    return struct.unpack('<Q', bytes)[0]
def b2d(bytes):
    return struct.unpack('<I', bytes)[0]
def b2w(bytes):
    return struct.unpack('<H', bytes)[0]
def b2threebytes(bytes):
    return bytes[0] + (bytes[1] << 8) + (bytes[2] << 16)


# oo2core_6_win64.dll要放到py脚本运行目录下，该dll网上可搜索下载。
def DecompressChunk(compressedData, decompressedDataSize):
    import ctypes
    lib= ctypes.CDLL('oo2core_6_win64.dll')

    buffer = bytearray(decompressedDataSize + 0x100)
    temp = ctypes.c_ubyte * len(buffer)

    decodedSize = lib.OodleLZ_Decompress(compressedData, len(compressedData), temp.from_buffer(buffer), decompressedDataSize, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    if decodedSize == 0:
        raise
    return buffer[:decodedSize]


def file_format(fstream, seek_num):
    '''
    单个（提取后的）文件（在存储时）的格式：
    将文件分为n块，分别使用Oodle压缩。

    Header区为：
    8+8+8+4+2+2+n*4
    8+8+8+4+2+2不解释。
    后面是n个int（n为文件分割的块数），记录着每块在文件中的偏移

    Data区为：
    n块，每块的前4字节为当前块（解压后）的大小，后面为使用Oodle压缩后的数据。
    '''
    #print('seek_num = ', seek_num)
    f = fstream
    f.seek(seek_num)
    decompressedSize        = b2q(f.read(8))
    date                    = b2q(f.read(8))
    compressedSize          = b2q(f.read(8))
    chunkDecompressedSize   = b2d(f.read(4))
    compression             = b2w(f.read(2))
    unknown                 = b2w(f.read(2))
    #print(decompressedSize, date, compressedSize, chunkDecompressedSize, compression, unknown)

    file_data = b''

    if compression == 0:
        data = f.read(decompressedSize)
        file_data += data
    else:
        chunkCount = decompressedSize // chunkDecompressedSize
        if decompressedSize % chunkDecompressedSize:
            chunkCount += 1
        #print(decompressedSize, compressedSize, chunkDecompressedSize, chunkCount)
        dataOffset = f.tell() + chunkCount * 4          # Data区的开头

        for i in range(chunkCount):
            chunkCompressedSize = b2d(f.read(4))
            nextChunkCompressedSizePos = f.tell()
                        
            f.seek(dataOffset)

            currentChunkSize = b2d(f.read(4))
            compressedData = f.read(chunkCompressedSize - 4)
            #print(currentChunkSize)

            decompressedData = DecompressChunk(compressedData, currentChunkSize)
            file_data += decompressedData
            dataOffset += chunkCompressedSize

            f.seek(nextChunkCompressedSizePos)          # 文件指针再回到Header区的n*4部分，以便从Header区n*4部分中读取下一个文件块的偏移

    return file_data


def parse_index(index_file_path):

    with open(index_file_path, 'rb')as f:
        index_data = file_format(f, 0)                  # index索引文件的格式也是这样的

        index_dic = {}
        # 令人窒息的操作，居然有一部分中文是utf-8，有一部分是gbk，暂时没想好怎么解决。好在绝大多数都是英文
        # b48fc077b7489dcf96c97968a364807637af072e 是utf-8
        # d7d79768d0565c4fd640dbbd71dd7fd0505ed6d9 是ANSI（gbk）
        index_data = index_data.decode('GBK', 'ignore')
        for line in index_data.split('\r\n'):
            if len(line.strip()) > 0:
                r = line.split('\t')
                hash, file = r[0], r[1]
                index_dic[hash] = file

        return index_dic


def file_extract(index_dic, archive_path, output_path):
    '''
    ZFS开头。

    Header区:
    可以多个Header区，格式如下：
    [IX]开头
    hash, fileOffset, fileCompressedSize, unknown

    Data区：
    Header区有多少条文件的记录，则对应Data区就有多少个对应的文件的数据，偏移由fileOffset来索引。
    每个文件的格式均如file_format()中所示。

    Header区有几个，Data区就对应有几个。
    '''

    files_num = 0

    with open(archive_path, 'rb')as f:
        magicId = f.read(4)
        if magicId != b'ZFS\x00':
            print("ZFS: Bad magic Id.")
            raise
        else:
            while True:
                chunkMagicId = f.read(4)
                if chunkMagicId != b'[IX]':
                    print("ZFS: Bad magic chunk Id.")
                    raise
                else:
                    nextChunk = b2d(f.read(4))
                    for i in range(0x1000):
                        hash = f.read(20)
                        hashString = hash.hex()

                        fileOffset = b2d(f.read(4))
                        fileCompressedSize = b2d(f.read(4))
                        unknown = b2threebytes(f.read(3))
                        flags = f.read(1)[0]

                        if fileOffset == 0:
                            continue

                        # 通过hash在索引中寻找文件名
                        if hashString in index_dic.keys():
                            file_name = index_dic[hashString]
                        else:
                            file_name = hashString
                            #print(hashString)
                        #print(archive_path)
                        #print(i, hashString, f.tell(), fileOffset, fileCompressedSize, file_name)
                        print(hashString, file_name)
                        files_num += 1

                        # 如果文件存在，则不再提取
                        output_file_path = os.path.join(output_path, file_name)
                        if os.path.exists(output_file_path) and os.path.getsize(output_file_path) > 128:
                            print('文件已存在，跳过')
                        else:
                            nextPos = f.tell()

                            # 提取文件
                            if flags == 1:
                                file_data = file_format(f, fileOffset)
                            elif flags == 0xff:
                                file_data = f.read(fileCompressedSize)

                            # 保存文件
                            temp_dir = os.path.dirname(output_file_path)
                            if not os.path.exists(temp_dir):
                                os.makedirs(temp_dir)
                            with open(output_file_path, 'wb')as wf:
                                wf.write(file_data)
                        
                            f.seek(nextPos)
                        
                    f.seek(nextChunk)

                if nextChunk == 0:      # NULL
                    break

        print('共有%d个文件' % files_num)
        return files_num


if __name__== '__main__':

    data_path = r'E:\steam\steamapps\common\古剑奇谭网络版\data'
    output_path = r'D:\gujianol\output'

    index_dic = parse_index(os.path.join(data_path, r'./_index'))

    files_num = 0

    for file in os.listdir(data_path):
        r = re.search(r'data\d{3}', file)
        if r:
            archive_path = os.path.join(data_path, file)
            files_num += file_extract(index_dic, archive_path, output_path)
    print('总文件数：', files_num)